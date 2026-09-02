"""GetXAPI client tests — normalization, budget gating, fail-open, caching.

Covers the GetXAPI integration contract (2026-09-02):

* ``normalize_tweet`` — tolerant normalization of flat + nested payloads,
  hostile rows skipped, stable x.com URL, matches the apipass_x shape.
* budget gating — a call is never made when ``can_afford_call`` is False
  (fail-open returns ``[]`` / ``None`` without hitting the network).
* fail-open without a key — never raises, never hits the network.
* Redis caching — a cached hit returns without a network call (zero burn).
"""

from __future__ import annotations

import asyncio

from rugmunchbot.tools import getxapi as m

# ══════════════════════════════════════════════════════════════════════════
# normalize_tweet (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_normalize_flat_shape():
    tweet = m.normalize_tweet(
        {
            "id": "123",
            "text": "$MOON is the next 100x, aping in",
            "username": "shillking",
            "created_at": "2026-09-02T10:00:00Z",
            "retweet_count": 12,
            "like_count": 340,
        }
    )
    assert tweet is not None
    assert tweet["handle"] == "shillking"
    assert tweet["url"] == "https://x.com/shillking/status/123"
    assert tweet["like_count"] == 340
    assert tweet["source"] == "getxapi"


def test_normalize_nested_shape():
    raw = {
        "tweet": {
            "id": "456",
            "full_text": "I deleted nothing. $PEPE long.",
            "user": {"username": "kolo", "name": "KOL One"},
            "public_metrics": {"retweet_count": 3, "like_count": 55, "reply_count": 1},
        }
    }
    tweet = m.normalize_tweet(raw)
    assert tweet is not None
    assert tweet["handle"] == "kolo"
    assert tweet["author_name"] == "KOL One"
    assert tweet["retweet_count"] == 3


def test_normalize_rejects_hostile_rows():
    assert m.normalize_tweet({}) is None
    assert m.normalize_tweet({"id": "1"}) is None  # no text
    assert m.normalize_tweet({"text": "hi"}) is None  # no id
    assert m.normalize_tweet(None) is None
    assert m.normalize_tweet("not a dict") is None


# ══════════════════════════════════════════════════════════════════════════
# Fail-open without a key
# ══════════════════════════════════════════════════════════════════════════


def test_search_tweets_fails_open_no_key(monkeypatch):
    monkeypatch.setattr(m, "api_key_configured", lambda: False)
    tweets = asyncio.run(m.search_tweets("exploit"))
    assert tweets == []


def test_get_user_tweets_fails_open_no_key(monkeypatch):
    monkeypatch.setattr(m, "api_key_configured", lambda: False)
    tweets = asyncio.run(m.get_user_tweets("peckshield"))
    assert tweets == []


def test_get_user_info_fails_open_no_key(monkeypatch):
    monkeypatch.setattr(m, "api_key_configured", lambda: False)
    info = asyncio.run(m.get_user_info("peckshield"))
    assert info is None


def test_get_trends_fails_open_no_key(monkeypatch):
    monkeypatch.setattr(m, "api_key_configured", lambda: False)
    trends = asyncio.run(m.get_trends())
    assert trends == []


# ══════════════════════════════════════════════════════════════════════════
# Budget gating — never call past the envelope
# ══════════════════════════════════════════════════════════════════════════


def test_search_tweets_skipped_when_budget_exhausted(monkeypatch):
    monkeypatch.setattr(m, "api_key_configured", lambda: True)
    monkeypatch.setattr(m, "can_afford_call", lambda: False)
    tweets = asyncio.run(m.search_tweets("exploit"))
    assert tweets == []


def test_get_user_tweets_skipped_when_budget_exhausted(monkeypatch):
    monkeypatch.setattr(m, "api_key_configured", lambda: True)
    monkeypatch.setattr(m, "can_afford_call", lambda: False)
    tweets = asyncio.run(m.get_user_tweets("peckshield"))
    assert tweets == []


# ══════════════════════════════════════════════════════════════════════════
# Redis caching — a cached hit costs zero calls
# ══════════════════════════════════════════════════════════════════════════


def test_search_tweets_served_from_cache(monkeypatch):
    """A cached result returns without a network call (zero burn)."""
    calls: list[str] = []

    async def _fake_get(path, params=None):
        calls.append(path)
        return {"data": [{"id": "1", "text": "exploit alert", "username": "x"}]}

    async def _fake_cache_get(key):
        return [
            {"tweet_id": "1", "text": "cached", "handle": "x", "url": "https://x.com/x/status/1"}
        ]

    async def _fake_cache_set(key, value, ttl):
        return None

    monkeypatch.setattr(m, "api_key_configured", lambda: True)
    monkeypatch.setattr(m, "can_afford_call", lambda: True)
    monkeypatch.setattr(m, "_get", _fake_get)
    monkeypatch.setattr(m, "_cache_get", _fake_cache_get)
    monkeypatch.setattr(m, "_cache_set", _fake_cache_set)

    tweets = asyncio.run(m.search_tweets("exploit"))
    assert tweets == [
        {"tweet_id": "1", "text": "cached", "handle": "x", "url": "https://x.com/x/status/1"}
    ]
    assert calls == []  # served from cache, no network call


def test_search_tweets_misses_cache_and_calls_api(monkeypatch):
    """A cache miss hits the API and populates the cache."""
    calls: list[str] = []

    async def _fake_get(path, params=None):
        calls.append(path)
        return {"data": [{"id": "1", "text": "exploit alert", "username": "x"}]}

    async def _fake_cache_get(key):
        return None  # miss

    cached: list[tuple] = []

    async def _fake_cache_set(key, value, ttl):
        cached.append((key, value, ttl))

    monkeypatch.setattr(m, "api_key_configured", lambda: True)
    monkeypatch.setattr(m, "can_afford_call", lambda: True)
    monkeypatch.setattr(m, "_get", _fake_get)
    monkeypatch.setattr(m, "_cache_get", _fake_cache_get)
    monkeypatch.setattr(m, "_cache_set", _fake_cache_set)

    tweets = asyncio.run(m.search_tweets("exploit"))
    assert calls == ["/tweet/advanced_search"]
    assert len(tweets) == 1
    assert tweets[0]["tweet_id"] == "1"
    assert len(cached) == 1  # populated the cache


# ══════════════════════════════════════════════════════════════════════════
# call_count / budget_remaining
# ══════════════════════════════════════════════════════════════════════════


def test_note_call_increments_counter(monkeypatch):
    """``_note_call`` bumps the in-memory counter and meters the budget."""
    recorded: list[str] = []

    def _fake_record_call(endpoint=""):
        recorded.append(endpoint)

    monkeypatch.setattr("rugmunchbot.tools.getxapi.record_call", _fake_record_call)
    before = m.call_count()
    m._note_call(endpoint="/user/tweets")
    assert m.call_count() == before + 1
    assert recorded == ["/user/tweets"]


def test_budget_remaining_shape(monkeypatch):
    monkeypatch.setattr(
        "rugmunchbot.tools.getxapi_budget.get_daily_usage",
        lambda: {"calls": 10, "spend": 0.01, "calls_remaining": 140},
    )
    report = m.budget_remaining()
    assert report["total_calls"] == 10
    assert report["calls_remaining"] == 140
    assert report["budget_calls"] == m.GETXAPI_DAILY_CALL_LIMIT
    assert report["total_budget"] == m.GETXAPI_TOTAL_BUDGET
    assert report["days_left"] > 0
