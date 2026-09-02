"""Trending topics + mention monitoring — GetXAPI trends, mentions, account status.

Covers the social-monitor contract (2026-09-02):

* ``is_crypto_trend`` — crypto-keyword filter over raw GetXAPI /trends rows.
* ``tweet_to_article`` — normalized GetXAPI mention tweet → ``NewsArticle``
  (hostile rows skipped, entities extracted, timestamps parsed, stable x.com
  URL).
* ``fetch_crypto_trends`` — ONE GetXAPI /trends call, crypto-filtered,
  budget-gated, fail-open without a key.
* ``fetch_mentions`` — ONE GetXAPI /user/mentions call, budget-gated,
  fail-open without a key.
* ``run_mentions`` — mentions → normalize → in-batch dedup → cross-run Redis
  dedup → RAG feed → mark-ingested (only after RAG succeeds).
* ``fetch_account_status`` / ``_is_suspended`` — /user/status, WARNING on
  suspension, fail-open.
* ``run_account_status_sweep`` — one /user/status per monitored account.
"""

from __future__ import annotations

import asyncio

from rugmunchbot.tools import social_monitor as m

# ══════════════════════════════════════════════════════════════════════════
# is_crypto_trend (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_is_crypto_trend_matches_keywords():
    assert m.is_crypto_trend({"name": "Bitcoin"})
    assert m.is_crypto_trend({"trend": "SOL"})
    assert m.is_crypto_trend({"query": "ethereum"})
    assert m.is_crypto_trend({"name": "Rug pull alert"})
    assert m.is_crypto_trend({"name": "Binance"})
    assert m.is_crypto_trend({"name": "BTC"})
    assert m.is_crypto_trend({"name": "meme coin"})
    assert m.is_crypto_trend({"name": "smart contract"})
    assert m.is_crypto_trend({"name": "layer 2"})


def test_is_crypto_trend_rejects_non_crypto():
    assert not m.is_crypto_trend({"name": "World Cup"})
    assert not m.is_crypto_trend({"name": "Election 2026"})
    assert not m.is_crypto_trend({"name": "Taylor Swift"})
    assert not m.is_crypto_trend({"name": "NFL"})
    assert not m.is_crypto_trend({"name": "Oscars"})


def test_is_crypto_trend_fails_open_on_hostile_rows():
    assert not m.is_crypto_trend(None)
    assert not m.is_crypto_trend({})
    assert not m.is_crypto_trend("Bitcoin")  # non-dict row
    assert not m.is_crypto_trend({"name": ""})


# ══════════════════════════════════════════════════════════════════════════
# tweet_to_article (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_tweet_to_article_basic():
    article = m.tweet_to_article(
        {
            "tweet_id": "123",
            "text": "@cryptorugmuncher great rug detection on $MOON today",
            "handle": "cryptouser",
            "author_name": "Crypto User",
            "created_at": "2026-09-02T10:00:00Z",
            "url": "https://x.com/cryptouser/status/123",
        },
    )
    assert article is not None
    assert article.source_channel == "@cryptouser"
    assert article.source_url == "https://x.com/cryptouser/status/123"
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
    assert m.tweet_to_article({"text": "a real mention here"}) is None


def test_tweet_to_article_parses_x_native_timestamp():
    article = m.tweet_to_article(
        {
            "tweet_id": "9",
            "text": "love the new @cryptorugmuncher scanner",
            "handle": "fan",
            "url": "https://x.com/fan/status/9",
            "created_at": "Sun Jan 25 13:05:46 +0000 2026",
        },
    )
    assert article is not None
    assert article.posted_at is not None
    assert article.posted_at.year == 2026


def test_tweet_to_article_falls_back_to_generic_source():
    article = m.tweet_to_article(
        {
            "tweet_id": "5",
            "text": "shoutout to the rugmunch team for the alert",
            "url": "https://x.com/z/status/5",
        },
    )
    assert article is not None
    assert article.source_channel == "social_monitor"


# ══════════════════════════════════════════════════════════════════════════
# fetch_crypto_trends (ONE call, budget-gated, fail-open)
# ══════════════════════════════════════════════════════════════════════════


def test_fetch_trends_fails_open_no_key(monkeypatch):
    from rugmunchbot.tools import getxapi

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: False)
    trends = asyncio.run(m.fetch_crypto_trends())
    assert trends == []


def test_fetch_trends_fails_open_budget_exhausted(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: False)
    trends = asyncio.run(m.fetch_crypto_trends())
    assert trends == []


def test_fetch_trends_filters_crypto(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    async def _get_trends():
        return [
            {"name": "Bitcoin"},
            {"name": "World Cup"},
            {"name": "SOL"},
            {"name": "Election 2026"},
            {"name": "Ethereum"},
        ]

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "get_trends", _get_trends)

    trends = asyncio.run(m.fetch_crypto_trends())
    names = [t["name"] for t in trends]
    assert names == ["Bitcoin", "SOL", "Ethereum"]


def test_fetch_trends_fails_open_on_exception(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    async def _boom():
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "get_trends", _boom)

    trends = asyncio.run(m.fetch_crypto_trends())
    assert trends == []


# ══════════════════════════════════════════════════════════════════════════
# fetch_mentions (ONE call, budget-gated, fail-open)
# ══════════════════════════════════════════════════════════════════════════


def test_fetch_mentions_fails_open_no_key(monkeypatch):
    from rugmunchbot.tools import getxapi

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: False)
    tweets = asyncio.run(m.fetch_mentions())
    assert tweets == []


def test_fetch_mentions_fails_open_budget_exhausted(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: False)
    tweets = asyncio.run(m.fetch_mentions())
    assert tweets == []


def test_fetch_mentions_returns_normalized(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    calls: list[str] = []

    async def _get_mentions(username: str, count: int = 20):
        calls.append(username)
        return [
            {
                "tweet_id": "1",
                "text": "@cryptorugmuncher great rug detection on $MOON",
                "handle": "cryptouser",
                "url": "https://x.com/cryptouser/status/1",
            },
            {
                "tweet_id": "2",
                "text": "love the new @cryptorugmuncher scanner",
                "handle": "fan",
                "url": "https://x.com/fan/status/2",
            },
        ]

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "get_user_mentions", _get_mentions)

    tweets = asyncio.run(m.fetch_mentions())
    assert len(tweets) == 2
    # Exactly ONE mentions call (the 24/day budget is hard).
    assert len(calls) == 1
    assert calls[0] == m.SOCIAL_MONITOR_USERNAME


def test_fetch_mentions_fails_open_on_exception(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    async def _boom(username: str, count: int = 20):
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "get_user_mentions", _boom)

    tweets = asyncio.run(m.fetch_mentions())
    assert tweets == []


# ══════════════════════════════════════════════════════════════════════════
# run_mentions (full pipeline, mocked getxapi + RAG)
# ══════════════════════════════════════════════════════════════════════════


def test_run_mentions_empty_fetch(monkeypatch):
    async def _empty(username: str = m.SOCIAL_MONITOR_USERNAME):
        return []

    monkeypatch.setattr(m, "fetch_mentions", _empty)
    counts = asyncio.run(m.run_mentions())
    assert counts["fetched"] == 0
    assert counts["articles"] == 0


def test_run_mentions_full_pipeline(monkeypatch):
    """Mentions → normalize → dedupe → RAG feed → mark-ingested."""
    tweets = [
        {
            "tweet_id": "1",
            "text": "@cryptorugmuncher great rug detection on $MOON",
            "handle": "cryptouser",
            "url": "https://x.com/cryptouser/status/1",
        },
        {
            "tweet_id": "2",
            "text": "love the new @cryptorugmuncher scanner",
            "handle": "fan",
            "url": "https://x.com/fan/status/2",
        },
    ]

    async def _fetch(username: str = m.SOCIAL_MONITOR_USERNAME):
        return tweets

    rag_calls: list[list] = []
    marked: list[list] = []

    async def _fake_rag(articles):
        rag_calls.append(articles)
        return {"stories": 2, "ingested": 2, "duplicates": 0, "errors": 0}

    def _fake_mark(articles, collection):
        marked.append(articles)
        return len(articles)

    monkeypatch.setattr(m, "fetch_mentions", _fetch)
    monkeypatch.setattr(m, "dedupe_articles", lambda a: a)  # no in-batch dupes
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))  # all fresh
    monkeypatch.setattr(m, "mark_articles_ingested", _fake_mark)
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.run_mentions())
    assert counts["fetched"] == 2
    assert counts["articles"] == 2
    assert counts["fresh"] == 2
    assert counts["skipped"] == 0
    assert counts["rag"]["ingested"] == 2
    # Marked only after RAG succeeded.
    assert len(marked) == 1
    assert len(rag_calls) == 1


def test_run_mentions_does_not_mark_on_rag_error(monkeypatch):
    """A RAG outage must NOT mark mentions ingested (they get re-read next tick)."""
    tweets = [
        {
            "tweet_id": "1",
            "text": "@cryptorugmuncher great rug detection on $MOON",
            "handle": "cryptouser",
            "url": "https://x.com/cryptouser/status/1",
        },
    ]

    async def _fetch(username: str = m.SOCIAL_MONITOR_USERNAME):
        return tweets

    marked: list[list] = []

    async def _fake_rag(articles):
        return {"stories": 0, "ingested": 0, "duplicates": 0, "errors": 1}

    def _fake_mark(articles, collection):
        marked.append(articles)
        return len(articles)

    monkeypatch.setattr(m, "fetch_mentions", _fetch)
    monkeypatch.setattr(m, "dedupe_articles", lambda a: a)
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))
    monkeypatch.setattr(m, "mark_articles_ingested", _fake_mark)
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.run_mentions())
    assert counts["rag"]["errors"] == 1
    assert marked == []  # NOT marked on RAG failure


def test_run_mentions_in_batch_dedup(monkeypatch):
    """Same mention surfaced multiple times collapses to one article."""
    tweets = [
        {
            "tweet_id": "1",
            "text": "@cryptorugmuncher great rug detection on $MOON",
            "handle": "cryptouser",
            "url": "https://x.com/cryptouser/status/1",
        },
        {
            "tweet_id": "1",
            "text": "@cryptorugmuncher great rug detection on $MOON",
            "handle": "other",
            "url": "https://x.com/other/status/1",
        },
    ]

    async def _fetch(username: str = m.SOCIAL_MONITOR_USERNAME):
        return tweets

    async def _fake_rag(articles):
        return {"stories": 1, "ingested": 1, "duplicates": 0, "errors": 0}

    monkeypatch.setattr(m, "fetch_mentions", _fetch)
    monkeypatch.setattr(m, "filter_seen_articles", lambda a, c: (a, 0))
    monkeypatch.setattr(m, "mark_articles_ingested", lambda a, c: len(a))
    monkeypatch.setattr("rugmunchbot.channels.telegram_news.ingest_into_rag", _fake_rag)

    counts = asyncio.run(m.run_mentions())
    # ``articles`` = raw normalized count (2); in-batch dedup collapses the
    # duplicate content → 1 fresh article fed to RAG.
    assert counts["articles"] == 2
    assert counts["fresh"] == 1


# ══════════════════════════════════════════════════════════════════════════
# fetch_account_status / _is_suspended
# ══════════════════════════════════════════════════════════════════════════


def test_is_suspended_detects_flat_and_nested():
    assert m._is_suspended({"suspended": True})
    assert m._is_suspended({"is_suspended": True})
    assert m._is_suspended({"user": {"suspended": True}})
    assert m._is_suspended({"data": {"is_suspended": True}})
    assert m._is_suspended({"status": "suspended"})
    assert m._is_suspended({"status": "deactivated"})
    assert m._is_suspended({"status": "not_found"})


def test_is_suspended_fails_open():
    assert not m._is_suspended(None)
    assert not m._is_suspended({})
    assert not m._is_suspended({"suspended": False})
    assert not m._is_suspended({"status": "active"})
    assert not m._is_suspended("suspended")  # non-dict


def test_fetch_account_status_fails_open_no_key(monkeypatch):
    from rugmunchbot.tools import getxapi

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: False)
    assert asyncio.run(m.fetch_account_status("zachxbt")) is None


def test_fetch_account_status_fails_open_budget_exhausted(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: False)
    assert asyncio.run(m.fetch_account_status("zachxbt")) is None


def test_fetch_account_status_returns_payload(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    async def _get_status(username: str):
        return {"username": username, "status": "active"}

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "get_user_status", _get_status)

    status = asyncio.run(m.fetch_account_status("zachxbt"))
    assert status is not None
    assert status["status"] == "active"


def test_fetch_account_status_fails_open_on_exception(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    async def _boom(username: str):
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "get_user_status", _boom)

    assert asyncio.run(m.fetch_account_status("zachxbt")) is None


# ══════════════════════════════════════════════════════════════════════════
# run_account_status_sweep (one call per monitored account)
# ══════════════════════════════════════════════════════════════════════════


def test_run_account_status_sweep_counts(monkeypatch):
    from rugmunchbot.tools import getxapi, getxapi_budget

    async def _get_status(username: str):
        if username == "zachxbt":
            return {"username": username, "suspended": True}
        return {"username": username, "status": "active"}

    monkeypatch.setattr(getxapi, "api_key_configured", lambda: True)
    monkeypatch.setattr(getxapi_budget, "can_afford_call", lambda: True)
    monkeypatch.setattr(getxapi, "get_user_status", _get_status)

    counts = asyncio.run(m.run_account_status_sweep(accounts=("zachxbt", "peckshield", "blockaid")))
    assert counts["checked"] == 3
    assert counts["suspended"] == 1


def test_run_account_status_sweep_empty(monkeypatch):
    counts = asyncio.run(m.run_account_status_sweep(accounts=()))
    assert counts["checked"] == 0
    assert counts["suspended"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Scheduled runner (channels.social_monitor)
# ══════════════════════════════════════════════════════════════════════════


def test_run_trends_disabled(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", False)
    assert asyncio.run(runner.run_trends()) == 0


def test_run_trends_returns_count(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    async def _fake_trends():
        return [{"name": "Bitcoin"}, {"name": "SOL"}]

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.social_monitor.fetch_crypto_trends", _fake_trends)
    assert asyncio.run(runner.run_trends()) == 2


def test_run_trends_fails_open_on_exception(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    async def _boom():
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.social_monitor.fetch_crypto_trends", _boom)
    assert asyncio.run(runner.run_trends()) == 0  # never raises


def test_run_mentions_runner_disabled(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", False)
    assert asyncio.run(runner.run_mentions()) == 0


def test_run_mentions_runner_returns_fresh_count(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    async def _fake_ingest():
        return {
            "username": "cryptorugmuncher",
            "fetched": 3,
            "articles": 3,
            "fresh": 2,
            "skipped": 1,
            "rag": {"ingested": 2},
        }

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.social_monitor.run_mentions", _fake_ingest)
    assert asyncio.run(runner.run_mentions()) == 2


def test_run_mentions_runner_fails_open_on_exception(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    async def _boom():
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.social_monitor.run_mentions", _boom)
    assert asyncio.run(runner.run_mentions()) == 0  # never raises


def test_run_account_status_runner_disabled(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", False)
    assert asyncio.run(runner.run_account_status()) == 0


def test_run_account_status_runner_returns_checked(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    async def _fake_sweep():
        return {"checked": 15, "suspended": 0}

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.social_monitor.run_account_status_sweep", _fake_sweep)
    assert asyncio.run(runner.run_account_status()) == 15


def test_run_account_status_runner_fails_open_on_exception(monkeypatch):
    from rugmunchbot.channels import social_monitor as runner

    async def _boom():
        raise RuntimeError("getxapi down")

    monkeypatch.setattr(runner, "SOCIAL_MONITOR_ENABLED", True)
    monkeypatch.setattr("rugmunchbot.tools.social_monitor.run_account_status_sweep", _boom)
    assert asyncio.run(runner.run_account_status()) == 0  # never raises
