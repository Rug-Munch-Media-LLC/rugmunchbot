"""Security keyword search — GetXAPI advanced_search → article, rotation, RAG feed.

Covers the security-search contract (2026-09-02):

* ``tweet_to_article`` — normalized GetXAPI tweet → ``NewsArticle`` (hostile
  rows skipped, entities extracted, timestamps parsed, stable x.com URL).
* ``fetch_security_search_tweets`` — ONE GetXAPI advanced_search per call,
  budget-gated, fail-open without a key, query rotation advances only on
  success.
* ``run_security_search`` — search → normalize → in-batch dedup → cross-run
  Redis dedup → RAG feed → mark-ingested (only after RAG succeeds).
"""

from __future__ import annotations

import asyncio

from rugmunchbot.tools import security_search as m

# ══════════════════════════════════════════════════════════════════════════
# tweet_to_article (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_tweet_to_article_basic():
    article = m.tweet_to_article(
        {
            "tweet_id": "123",
            "text": "$MOON exploit detected — $2M drained from the pool",
            "handle": "peckshield",
            "author_name": "PeckShieldAlert",
            "created_at": "2026-09-02T10:00:00Z",
            "url": "https://x.com/peckshield/status/123",
        },
    )
    assert article is not None
    assert article.source_channel == "@peckshield"
    assert article.source_url == "https://x.com/peckshield/status/123"
    assert article.category == "crypto_news"
    assert "$MOON" in article.entities
    assert article.posted_at is not None
    assert article.posted_at.tzinfo is not None  # aware UTC


def test_tweet_to_article_skips_hostile_rows():
    assert m.tweet_to_article(None) is None
    assert m.tweet_to_article({}) is None
    # Too short (below min chars).
    assert m.tweet_to_article({"text": "hi", "url": "https://x.com/a/status/1"}) is None
    # No URL.
    assert m.tweet_to_article({"text": "a real security alert here"}) is None


def test_tweet_to_article_parses_x_native_timestamp():
    article = m.tweet_to_article(
        {
            "tweet_id": "9",
            "text": "Ledger firmware update — please verify the source",
            "handle": "ledger",
            "url": "https://x.com/ledger/status/9",
            "created_at": "Sun Jan 25 13:05:46 +0000 2026",
        },
    )
    assert article is not None
    assert article.posted_at is not None
    assert article.posted_at.year == 2026


def test_tweet_to_article_falls_back_to_generic_source():
    # No handle → generic source_channel (still a valid article).
    article = m.tweet_to_article(
        {
            "tweet_id": "5",
            "text": "Scam wallet warning — stay safe",
            "url": "https://x.com/z/status/5",
        },
    )
    assert article is not None
    assert article.source_channel == "security_search"


# ══════════════════════════════════════════════════════════════════════════
# Query rotation
# ══════════════════════════════════════════════════════════════════════════


def test_query_rotation_wraps(monkeypatch):
    monkeypatch.setenv("RMI_SECURITY_SEARCH_QUERY_INDEX", "0")
    assert m._next_query_index() == 0
    assert m.SECURITY_SEARCH_QUERIES[0].startswith('"exploit"')
    # Advance wraps around the list.
    monkeypatch.setenv("RMI_SECURITY_SEARCH_QUERY_INDEX", str(len(m.SECURITY_SEARCH_QUERIES) - 1))
    assert m._next_query_index() == len(m.SECURITY_SEARCH_QUERIES) - 1
    m._advance_query_index()
    assert m._next_query_index() == 0  # wrapped


def test_queries_are_distinct_and_high_signal():
    # Every query excludes retweets and demands a minimum engagement floor.
    assert len(m.SECURITY_SEARCH_QUERIES) == 6
    for q in m.SECURITY_SEARCH_QUERIES:
        assert "-is:retweet" in q
        assert "min_faves:" in q


# ══════════════════════════════════════════════════════════════════════════
# fetch_security_search_tweets (ONE call, budget-gated, fail-open)
# ══════════════════════════════════════════════════════════════════════════


def test_fetch_fails_open_no_key(monkeypatch):
    from rugmunchbot.tools import getxapi

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: False)
    tweets = asyncio.run(m.fetch_security_search_tweets())
    assert tweets == []


def test_fetch_fails_open_budget_exhausted(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: False)
    tweets = asyncio.run(m.fetch_security_search_tweets())
    assert tweets == []


def test_fetch_rotates_and_returns_normalized(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    calls: list[str] = []

    async def _search_tweets(query: str, count: int = 20):
        calls.append(query)
        return [
            {
                "tweet_id": "1",
                "text": "$MOON exploit — $2M drained",
                "handle": "peckshield",
                "url": "https://x.com/peckshield/status/1",
            },
            {
                "tweet_id": "2",
                "text": "Ledger firmware update — verify source",
                "handle": "ledger",
                "url": "https://x.com/ledger/status/2",
            },
        ]

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "search_tweets", _search_tweets)
    monkeypatch.setenv("RMI_SECURITY_SEARCH_QUERY_INDEX", "0")

    tweets = asyncio.run(m.fetch_security_search_tweets())
    assert len(tweets) == 2
    # Exactly ONE search call (the 48/day budget is hard).
    assert len(calls) == 1
    assert calls[0] == m.SECURITY_SEARCH_QUERIES[0]
    # Rotation advanced for the next run.
    assert m._next_query_index() == 1


def test_fetch_does_not_advance_on_failure(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    async def _boom(query: str, count: int = 20):
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "search_tweets", _boom)
    monkeypatch.setenv("RMI_SECURITY_SEARCH_QUERY_INDEX", "0")

    tweets = asyncio.run(m.fetch_security_search_tweets())
    assert tweets == []
    # A failed tick retries the SAME query next time (rotation not advanced).
    assert m._next_query_index() == 0


# ══════════════════════════════════════════════════════════════════════════
# run_security_search (full pipeline, mocked getxapi + RAG)
# ══════════════════════════════════════════════════════════════════════════


def test_run_security_search_empty_fetch(monkeypatch):
    async def _empty():
        return []

    monkeypatch.setattr(m, "fetch_security_search_tweets", _empty)
    counts = asyncio.run(m.run_security_search())
    assert counts["fetched"] == 0
    assert counts["articles"] == 0


def test_run_security_search_full_pipeline(monkeypatch):
    """Search → normalize → dedupe → RAG feed → mark-ingested."""
    tweets = [
        {
            "tweet_id": "1",
            "text": "$MOON exploit — $2M drained",
            "handle": "peckshield",
            "url": "https://x.com/peckshield/status/1",
        },
        {
            "tweet_id": "2",
            "text": "Ledger firmware update — verify source",
            "handle": "ledger",
            "url": "https://x.com/ledger/status/2",
        },
    ]

    async def _fetch():
        return tweets

    rag_calls: list[list] = []
    marked: list[list] = []

    async def _fake_rag(articles):
        rag_calls.append(articles)
        return {"stories": 2, "ingested": 2, "duplicates": 0, "errors": 0}

    def _fake_mark(articles, collection):
        marked.append(articles)
        return len(articles)

    monkeypatch.setattr(m, "fetch_security_search_tweets", _fetch)
    monkeypatch.setattr(m, "dedupe_articles", lambda a: a)  # no in-batch dupes
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))  # all fresh
    monkeypatch.setattr(m, "mark_articles_ingested", _fake_mark)
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.run_security_search())
    assert counts["fetched"] == 2
    assert counts["articles"] == 2
    assert counts["fresh"] == 2
    assert counts["skipped"] == 0
    assert counts["rag"]["ingested"] == 2
    # Marked only after RAG succeeded.
    assert len(marked) == 1
    assert len(rag_calls) == 1


def test_run_security_search_does_not_mark_on_rag_error(monkeypatch):
    """A RAG outage must NOT mark tweets ingested (they get re-read next tick)."""
    tweets = [
        {
            "tweet_id": "1",
            "text": "$MOON exploit — $2M drained",
            "handle": "peckshield",
            "url": "https://x.com/peckshield/status/1",
        },
    ]

    async def _fetch():
        return tweets

    marked: list[list] = []

    async def _fake_rag(articles):
        return {"stories": 0, "ingested": 0, "duplicates": 0, "errors": 1}

    def _fake_mark(articles, collection):
        marked.append(articles)
        return len(articles)

    monkeypatch.setattr(m, "fetch_security_search_tweets", _fetch)
    monkeypatch.setattr(m, "dedupe_articles", lambda a: a)
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))
    monkeypatch.setattr(m, "mark_articles_ingested", _fake_mark)
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.run_security_search())
    assert counts["rag"]["errors"] == 1
    assert marked == []  # NOT marked on RAG failure


def test_run_security_search_in_batch_dedup(monkeypatch):
    """Same tweet surfaced under multiple queries collapses to one article."""
    tweets = [
        {
            "tweet_id": "1",
            "text": "$MOON exploit — $2M drained",
            "handle": "peckshield",
            "url": "https://x.com/peckshield/status/1",
        },
        {
            "tweet_id": "1",
            "text": "$MOON exploit — $2M drained",
            "handle": "blockaid",
            "url": "https://x.com/blockaid/status/1",
        },
    ]

    async def _fetch():
        return tweets

    async def _fake_rag(articles):
        return {"stories": 1, "ingested": 1, "duplicates": 0, "errors": 0}

    monkeypatch.setattr(m, "fetch_security_search_tweets", _fetch)
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))
    monkeypatch.setattr(m, "mark_articles_ingested", lambda a, c: len(a))
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.run_security_search())
    # ``articles`` = raw normalized count (2); in-batch dedup collapses the
    # duplicate content → 1 fresh article fed to RAG.
    assert counts["articles"] == 2
    assert counts["fresh"] == 1


# ══════════════════════════════════════════════════════════════════════════
# Scheduled runner (channels.security_search.run_security_search)
# ══════════════════════════════════════════════════════════════════════════


def test_run_runner_disabled(monkeypatch):
    from rugmunchbot.channels import security_search as runner

    monkeypatch.setattr(runner, "SECURITY_SEARCH_ENABLED", False)
    assert asyncio.run(runner.run_security_search()) == 0


def test_run_runner_returns_fresh_count(monkeypatch):
    from rugmunchbot.channels import security_search as runner

    async def _fake_ingest():
        return {
            "query": '"exploit" OR "hack"',
            "fetched": 3,
            "articles": 3,
            "fresh": 2,
            "skipped": 1,
            "rag": {"ingested": 2},
        }

    monkeypatch.setattr(runner, "SECURITY_SEARCH_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.security_search.run_security_search", _fake_ingest)
    assert asyncio.run(runner.run_security_search()) == 2


def test_run_runner_fails_open_on_exception(monkeypatch):
    from rugmunchbot.channels import security_search as runner

    async def _boom():
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(runner, "SECURITY_SEARCH_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.security_search.run_security_search", _boom)
    assert asyncio.run(runner.run_security_search()) == 0  # never raises
