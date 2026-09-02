"""X/Twitter content ingestion — tweet→article, budget-bounded fetch, RAG feed.

Covers the X ingestion contract (2026-09-02):

* ``tweet_to_article`` — normalized ApiPass tweet → ``NewsArticle`` (hostile
  rows skipped, entities extracted, timestamps parsed, stable x.com URL).
* ``fetch_security_tweets`` — budget-bounded round-robin over the security
  accounts; fail-open without a key; never exceeds ApiPass creates.
* ``ingest_x_news`` — fetch → normalize → in-batch dedup → cross-run Redis
  dedup → RAG feed → mark-ingested (only after RAG succeeds).
"""

from __future__ import annotations

import asyncio

from rugmunchbot.tools import x_ingest as m

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
        account="peckshield",
    )
    assert article is not None
    assert article.source_channel == "@peckshield"
    assert article.source_url == "https://x.com/peckshield/status/123"
    assert article.category == "crypto_news"
    assert "$MOON" in article.entities
    assert article.posted_at is not None
    assert article.posted_at.tzinfo is not None  # aware UTC


def test_tweet_to_article_skips_hostile_rows():
    assert m.tweet_to_article(None, account="x") is None
    assert m.tweet_to_article({}, account="x") is None
    # Too short (below min chars).
    assert (
        m.tweet_to_article({"text": "hi", "url": "https://x.com/a/status/1"}, account="x") is None
    )
    # No URL.
    assert m.tweet_to_article({"text": "a real security alert here"}, account="x") is None


def test_tweet_to_article_parses_x_native_timestamp():
    article = m.tweet_to_article(
        {
            "tweet_id": "9",
            "text": "Ledger firmware update — please verify the source",
            "handle": "ledger",
            "url": "https://x.com/ledger/status/9",
            "created_at": "Wed Sep 02 10:00:00 +0000 2026",
        },
        account="ledger",
    )
    assert article is not None
    assert article.posted_at is not None
    assert article.posted_at.year == 2026


def test_tweet_to_article_uses_account_fallback_handle():
    article = m.tweet_to_article(
        {
            "tweet_id": "5",
            "text": "Scam wallet warning — stay safe",
            "url": "https://x.com/z/status/5",
        },
        account="zachxbt",
    )
    assert article is not None
    assert article.source_channel == "@zachxbt"


# ══════════════════════════════════════════════════════════════════════════
# fetch_security_tweets (budget-bounded, fail-open)
# ══════════════════════════════════════════════════════════════════════════


def test_fetch_security_tweets_fails_open_no_key(monkeypatch):
    from rugmunchbot.tools import apipass_x

    monkeypatch.setattr(apipass_x, "api_key_configured", lambda: False)
    tweets = asyncio.run(m.fetch_security_tweets())
    assert tweets == []


def test_fetch_security_tweets_budget_exhausted(monkeypatch):
    from rugmunchbot.tools import apipass_x

    monkeypatch.setattr(apipass_x, "api_key_configured", lambda: True)
    monkeypatch.setattr(apipass_x, "task_creates_remaining", lambda: 0)  # no budget
    tweets = asyncio.run(m.fetch_security_tweets())
    assert tweets == []


def test_fetch_security_tweets_round_robin_and_attribution(monkeypatch):
    from rugmunchbot.tools import apipass_x

    fetched: list[str] = []

    async def _fetch_user_tweets(handle: str, limit: int = 10):
        fetched.append(handle)
        return [
            {
                "tweet_id": f"{handle}-1",
                "text": "security alert one",
                "url": f"https://x.com/{handle}/status/1",
            },
            {
                "tweet_id": f"{handle}-2",
                "text": "security alert two",
                "url": f"https://x.com/{handle}/status/2",
            },
        ]

    monkeypatch.setattr(apipass_x, "api_key_configured", lambda: True)
    monkeypatch.setattr(apipass_x, "task_creates_remaining", lambda: 100)  # plenty
    monkeypatch.setattr(apipass_x, "fetch_user_tweets", _fetch_user_tweets)
    monkeypatch.setattr(m, "X_INGEST_MAX_ACCOUNTS_PER_TICK", 2)
    monkeypatch.setenv("RMI_X_INGEST_ACCOUNT_INDEX", "0")
    tweets = asyncio.run(m.fetch_security_tweets())
    assert len(fetched) == 2  # capped at 2 accounts
    assert len(tweets) == 4
    # Attribution: every tweet carries its account handle.
    assert all(t.get("handle") for t in tweets)


# ══════════════════════════════════════════════════════════════════════════
# ingest_x_news (full pipeline, mocked apipass + RAG)
# ══════════════════════════════════════════════════════════════════════════


def test_ingest_x_news_empty_fetch(monkeypatch):
    async def _empty():
        return []

    monkeypatch.setattr(m, "fetch_security_tweets", _empty)
    counts = asyncio.run(m.ingest_x_news())
    assert counts["fetched"] == 0
    assert counts["articles"] == 0


def test_ingest_x_news_full_pipeline(monkeypatch):
    """Fetch → normalize → dedupe → RAG feed → mark-ingested."""
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

    monkeypatch.setattr(m, "fetch_security_tweets", _fetch)
    monkeypatch.setattr(m, "dedupe_articles", lambda a: a)  # no in-batch dupes
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))  # all fresh
    monkeypatch.setattr(m, "mark_articles_ingested", _fake_mark)
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.ingest_x_news())
    assert counts["fetched"] == 2
    assert counts["articles"] == 2
    assert counts["fresh"] == 2
    assert counts["skipped"] == 0
    assert counts["rag"]["ingested"] == 2
    # Marked only after RAG succeeded.
    assert len(marked) == 1
    assert len(rag_calls) == 1


def test_ingest_x_news_does_not_mark_on_rag_error(monkeypatch):
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

    monkeypatch.setattr(m, "fetch_security_tweets", _fetch)
    monkeypatch.setattr(m, "dedupe_articles", lambda a: a)
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))
    monkeypatch.setattr(m, "mark_articles_ingested", _fake_mark)
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.ingest_x_news())
    assert counts["rag"]["errors"] == 1
    assert marked == []  # NOT marked on RAG failure


def test_ingest_x_news_in_batch_dedup(monkeypatch):
    """Same tweet surfaced under two accounts collapses to one article."""
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

    monkeypatch.setattr(m, "fetch_security_tweets", _fetch)
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))
    monkeypatch.setattr(m, "mark_articles_ingested", lambda a, c: len(a))
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.ingest_x_news())
    # ``articles`` = raw normalized count (2); in-batch dedup collapses the
    # duplicate content → 1 fresh article fed to RAG.
    assert counts["articles"] == 2
    assert counts["fresh"] == 1


# ══════════════════════════════════════════════════════════════════════════
# Scheduled runner (channels.x_ingest.run_x_ingest)
# ══════════════════════════════════════════════════════════════════════════


def test_run_x_ingest_disabled(monkeypatch):
    from rugmunchbot.channels import x_ingest as runner

    monkeypatch.setattr(runner, "X_INGEST_ENABLED", False)
    assert asyncio.run(runner.run_x_ingest()) == 0


def test_run_x_ingest_returns_fresh_count(monkeypatch):
    from rugmunchbot.channels import x_ingest as runner

    async def _fake_ingest():
        return {"fetched": 3, "articles": 3, "fresh": 2, "skipped": 1, "rag": {"ingested": 2}}

    monkeypatch.setattr(runner, "X_INGEST_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.x_ingest.ingest_x_news", _fake_ingest)
    assert asyncio.run(runner.run_x_ingest()) == 2


def test_run_x_ingest_fails_open_on_exception(monkeypatch):
    from rugmunchbot.channels import x_ingest as runner

    async def _boom():
        raise RuntimeError("apipass down")

    monkeypatch.setattr(runner, "X_INGEST_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.x_ingest.ingest_x_news", _boom)
    assert asyncio.run(runner.run_x_ingest()) == 0  # never raises
