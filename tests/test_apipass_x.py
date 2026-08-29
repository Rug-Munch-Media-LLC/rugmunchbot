"""ApiPass X source — task-flow, normalization, budget, dedup (mocked transports).

Covers the ApiPass integration contract (OpenAPI-verified 2026-08-29):

* createTask → taskId → recordInfo poll → resultJson flow
* hourly budget (≤3 task creations/hr, polls free)
* fail-open without a key (never raises, never hits the network)
* tolerant normalization of messy provider payloads (flat + nested shapes)
* embed→dedup + rerank pipeline (embedder/reranker patched)
"""

from __future__ import annotations

import asyncio

from rugmunchbot.tools import apipass_x as m

# ══════════════════════════════════════════════════════════════════════════
# Normalization (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_normalize_flat_shape():
    tweet = m.normalize_tweet(
        {
            "tweet_id": "123",
            "text": "$MOON is the next 100x, aping in",
            "username": "shillking",
            "created_at": "2026-08-29T10:00:00Z",
            "retweet_count": 12,
            "like_count": 340,
        }
    )
    assert tweet is not None
    assert tweet["handle"] == "shillking"
    assert tweet["url"] == "https://x.com/shillking/status/123"
    assert tweet["like_count"] == 340


def test_normalize_nested_graphql_shape():
    raw = {
        "tweet": {
            "rest_id": "456",
            "full_text": "I deleted nothing. $PEPE long.",
            "core": {
                "user_results": {"result": {"legacy": {"screen_name": "kolo", "name": "KOL One"}}}
            },
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
    assert m.normalize_tweet({"tweet_id": "1"}) is None  # no text
    assert m.normalize_tweet({"text": "no id"}) is None  # no id
    assert m.normalize_tweet(None) is None
    assert m.normalize_tweet("string") is None


def test_extract_tweets_walks_containers_and_dedupes():
    payload = {
        "data": {
            "tweets": [
                {"tweet_id": "1", "text": "first"},
                {"tweet_id": "2", "text": "second"},
                {"tweet_id": "1", "text": "duplicate"},
            ]
        }
    }
    tweets = m._extract_tweets(payload)
    assert [t["tweet_id"] for t in tweets] == ["1", "2"]


# ══════════════════════════════════════════════════════════════════════════
# Rate limiter
# ══════════════════════════════════════════════════════════════════════════


def test_rate_limiter_hard_cap(monkeypatch):
    monkeypatch.setattr(m, "MAX_CALLS_PER_HOUR", 3)
    m._task_created_at.clear()
    assert m.task_creates_remaining() == 3
    m._note_task_created()
    m._note_task_created()
    m._note_task_created()
    assert m.task_creates_remaining() == 0
    assert not m._task_create_allowed()  # 4th call blocked


def test_rate_limiter_disabled_at_zero(monkeypatch):
    monkeypatch.setattr(m, "MAX_CALLS_PER_HOUR", 0)
    m._task_created_at.clear()
    assert not m._task_create_allowed()


# ══════════════════════════════════════════════════════════════════════════
# Task flow (respx-mocked HTTP, key patched in env)
# ══════════════════════════════════════════════════════════════════════════


def test_create_task_success(monkeypatch, respx_mock):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_test_key")
    m._task_created_at.clear()
    respx_mock.post(f"{m.APIPASS_BASE_URL}/api/v1/jobs/createTask").mock(
        return_value=httpx_response(200, {"code": 200, "data": {"taskId": "task-1"}})
    )
    task_id = asyncio.run(
        m.create_task(m.APIPASS_TWITTER_MODEL, {"endpoint": "twitter/simple/user-tweets"})
    )
    assert task_id == "task-1"
    assert m.task_creates_remaining() == m.MAX_CALLS_PER_HOUR - 1


def test_create_task_fails_open_no_key(monkeypatch, respx_mock):
    monkeypatch.delenv("APIPASS_API_KEY", raising=False)
    monkeypatch.setattr(m, "_resolve_api_key", lambda: "")
    respx_mock.post(f"{m.APIPASS_BASE_URL}/api/v1/jobs/createTask").mock(
        return_value=httpx_response(200, {"code": 200, "data": {"taskId": "task-1"}})
    )
    task_id = asyncio.run(m.create_task("twitter-api-simple", {}))
    assert task_id is None
    assert not respx_mock.calls  # no network when unconfigured


def test_create_task_rate_limited_skips_network(monkeypatch, respx_mock):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_test_key")
    monkeypatch.setattr(m, "MAX_CALLS_PER_HOUR", 0)
    respx_mock.post(f"{m.APIPASS_BASE_URL}/api/v1/jobs/createTask").mock(
        return_value=httpx_response(200, {"data": {"taskId": "task-1"}})
    )
    task_id = asyncio.run(m.create_task("twitter-api-simple", {}))
    assert task_id is None
    assert not respx_mock.calls


def test_get_task_returns_record(monkeypatch, respx_mock):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_test_key")
    respx_mock.get(f"{m.APIPASS_BASE_URL}/api/v1/jobs/recordInfo").mock(
        return_value=httpx_response(
            200,
            {
                "code": 200,
                "data": {"taskId": "task-1", "state": "success", "resultJson": {"tweet_id": "9"}},
            },
        )
    )
    record = asyncio.run(m.get_task("task-1"))
    assert record is not None
    assert record["state"] == "success"


def test_get_task_fails_open_on_http_error(monkeypatch, respx_mock):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_test_key")
    respx_mock.get(f"{m.APIPASS_BASE_URL}/api/v1/jobs/recordInfo").mock(
        return_value=httpx_response(500, {})
    )
    assert asyncio.run(m.get_task("task-1")) is None


def test_fetch_tweet_detail_real_contract(monkeypatch, respx_mock):
    """One real tweet via createTask→poll→normalize (the API pull contract)."""
    monkeypatch.setenv("APIPASS_API_KEY", "apk_test_key")
    m._task_created_at.clear()

    async def _fake_poll(model, input_body, **kw):
        return {
            "tweet": {
                "tweet_id": "777",
                "full_text": "$MOON is a gem",
                "user": {"screen_name": "k"},
                "public_metrics": {"like_count": 42},
            }
        }

    monkeypatch.setattr(m, "run_task", _fake_poll)
    tweet = asyncio.run(m.fetch_tweet_detail("777"))
    assert tweet is not None
    assert tweet["tweet_id"] == "777"
    assert tweet["source"] == "apipass_x"
    assert tweet["url"] == "https://x.com/k/status/777"


def test_fetch_user_tweets_polls_task(monkeypatch, respx_mock):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_test_key")
    m._task_created_at.clear()
    respx_mock.post(f"{m.APIPASS_BASE_URL}/api/v1/jobs/createTask").mock(
        return_value=httpx_response(200, {"code": 200, "data": {"taskId": "task-ut"}})
    )
    # First poll: pending; second: success with two tweets.
    respx_mock.get(f"{m.APIPASS_BASE_URL}/api/v1/jobs/recordInfo").mock(
        side_effect=[
            httpx_response(200, {"code": 200, "data": {"taskId": "task-ut", "state": "pending"}}),
            httpx_response(
                200,
                {
                    "code": 200,
                    "data": {
                        "taskId": "task-ut",
                        "state": "success",
                        "resultJson": {
                            "data": [
                                {"tweet_id": "1", "text": "$PEPE long", "username": "kolo"},
                                {"tweet_id": "2", "text": "market update", "username": "kolo"},
                            ]
                        },
                    },
                },
            ),
        ]
    )
    monkeypatch.setattr(m, "POLL_INTERVAL_S", 0.01)
    tweets = asyncio.run(m.fetch_user_tweets("kolo", limit=5))
    assert len(tweets) == 2
    assert tweets[0]["handle"] == "kolo"


def test_search_tweets_sorts_by_engagement(monkeypatch):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_test_key")

    async def _fake_run(model, input_body, **kw):
        return {
            "data": [
                {
                    "tweet_id": "a",
                    "text": "$MOON pump",
                    "username": "x",
                    "like_count": 1,
                    "retweet_count": 0,
                },
                {
                    "tweet_id": "b",
                    "text": "$MOON gem",
                    "username": "y",
                    "like_count": 100,
                    "retweet_count": 50,
                },
            ]
        }

    monkeypatch.setattr(m, "run_task", _fake_run)
    tweets = asyncio.run(m.search_tweets("$MOON", limit=5))
    assert tweets[0]["tweet_id"] == "b"  # higher engagement first


# ══════════════════════════════════════════════════════════════════════════
# Embed → dedup (embedder patched)
# ══════════════════════════════════════════════════════════════════════════


def test_semantic_dedup_detects_near_duplicate(monkeypatch):
    m.reset_embed_availability()
    m._recent_vectors.clear()

    # Deterministic fake embedder: "same claim" tokens → near-identical vectors.
    async def _fake_embed(text: str) -> list[float]:
        vec = [0.0] * 8
        if "$MOON" in text:
            vec[0] = 1.0
        if "gem" in text:
            vec[1] = 1.0
        if "long" in text:
            vec[2] = 1.0
        return vec

    monkeypatch.setattr(m, "get_embedding", _fake_embed)
    first = asyncio.run(m.semantic_dedup("$MOON is a gem, going long"))
    assert first is False  # new claim
    dup = asyncio.run(m.semantic_dedup("$MOON is a gem, long"))
    assert dup is True  # near-duplicate caught


def test_semantic_dedup_fails_open_when_embedder_down(monkeypatch):
    m.reset_embed_availability()

    async def _empty_embed(text: str) -> list[float]:
        return []

    monkeypatch.setattr(m, "get_embedding", _empty_embed)
    assert asyncio.run(m.semantic_dedup("anything")) is False  # never blocks


# ══════════════════════════════════════════════════════════════════════════
# Rerank (reranker patched)
# ══════════════════════════════════════════════════════════════════════════


def test_rerank_tweets_orders_by_relevance(monkeypatch):
    tweets = [
        {"tweet_id": "1", "text": "irrelevant noise"},
        {"tweet_id": "2", "text": "$MOON rug warning KOL deleted"},
    ]

    async def _fake_rerank(query, docs, top_n, tier="live"):
        return [{"index": 1, "relevance_score": 0.98}, {"index": 0, "relevance_score": 0.02}]

    monkeypatch.setattr(m, "rerank_results", _fake_rerank)
    ranked = asyncio.run(m.rerank_tweets(tweets, "shill warning", top_n=2))
    assert ranked[0]["tweet_id"] == "2"


def test_rerank_tweets_fails_open_keeps_order(monkeypatch):
    tweets = [{"tweet_id": "1", "text": "a"}, {"tweet_id": "2", "text": "b"}]

    async def _broken(query, docs, top_n, tier="live"):
        raise RuntimeError("reranker down")

    monkeypatch.setattr(m, "rerank_results", _broken)
    ranked = asyncio.run(m.rerank_tweets(tweets, "q", top_n=2))
    assert [t["tweet_id"] for t in ranked] == ["1", "2"]


# ══════════════════════════════════════════════════════════════════════════
# Key resolution
# ══════════════════════════════════════════════════════════════════════════


def test_key_resolution_env_first(monkeypatch):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_env_key")
    assert m._resolve_api_key() == "apk_env_key"
    assert m.api_key_configured()


def test_key_resolution_falls_back_to_key_file(monkeypatch, tmp_path):
    monkeypatch.delenv("APIPASS_API_KEY", raising=False)
    key_file = tmp_path / "apipass_api_key"
    key_file.write_text("apk_file_key\n")
    monkeypatch.setattr(m, "_KEY_FILE", str(key_file))
    assert m._resolve_api_key() == "apk_file_key"
    assert m.api_key_configured()


def test_key_resolution_file_missing_falls_to_gopass(monkeypatch, tmp_path):
    monkeypatch.delenv("APIPASS_API_KEY", raising=False)
    monkeypatch.setattr(m, "_KEY_FILE", str(tmp_path / "missing"))

    def _fake_gopass(*args, **kwargs):
        return b"apk_gopass_key\n"

    monkeypatch.setattr(m.subprocess, "check_output", _fake_gopass)
    assert m._resolve_api_key() == "apk_gopass_key"


def test_key_resolution_env_beats_file(monkeypatch, tmp_path):
    monkeypatch.setenv("APIPASS_API_KEY", "apk_env_key")
    key_file = tmp_path / "apipass_api_key"
    key_file.write_text("apk_file_key\n")
    monkeypatch.setattr(m, "_KEY_FILE", str(key_file))
    assert m._resolve_api_key() == "apk_env_key"


def test_default_budget_is_six_per_hour(monkeypatch):
    monkeypatch.delenv("RMI_APIPASS_MAX_CALLS_PER_HOUR", raising=False)
    import importlib

    importlib.reload(m)
    assert m.MAX_CALLS_PER_HOUR == 6  # ≤6 task-creations/hr ≈ 3 credits/hr


def test_key_resolution_empty_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("APIPASS_API_KEY", raising=False)
    monkeypatch.setattr(m, "_KEY_FILE", str(tmp_path / "missing"))
    monkeypatch.setattr(m.subprocess, "check_output", _fail_gopass)
    assert m._resolve_api_key() == ""
    assert not m.api_key_configured()


def _fail_gopass(*args, **kwargs):
    raise subprocess_err()


class _SubprocessError(Exception):
    pass


def subprocess_err():
    try:
        raise m.subprocess.CalledProcessError(1, ["gopass"])
    except m.subprocess.CalledProcessError as exc:
        return exc


def httpx_response(status: int, json_body: dict):
    import httpx

    return httpx.Response(status, json=json_body, request=httpx.Request("GET", "http://test"))
