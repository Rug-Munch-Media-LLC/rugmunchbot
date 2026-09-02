"""GetXAPI (X/Twitter) data source — real tweet intel at $0.001/call.

Why this exists
---------------
Replaces the ApiPass X source (``tools.apipass_x``) for the X-ingestion lane.
GetXAPI (``api.getxapi.com``) fronts the X API with a single bearer key and a
flat $0.001/call price (~20 tweets per call, no endpoint quotas). This module
is the first-party HTTP client that turns GetXAPI into security-intel data:
search / user-timeline / user-info / mentions / trends / user-status, each
returning parsed JSON, each fail-open.

Config + budget live in sibling modules (single source of truth, no dup):
* ``tools.getxapi_config`` — API key resolution (env → key file → gopass),
  base URL, daily/lifetime budget numbers, monitored accounts.
* ``tools.getxapi_budget`` — Redis-backed daily-call + total-spend accounting
  (``can_afford_call`` / ``record_call``). Every network call here is gated on
  ``can_afford_call`` and metered via ``record_call`` so the $5 envelope is
  never overspent.

Budget doctrine (hard, enforced here)
-------------------------------------
* $5 = 4,500 calls, must last 30+ days → **max 150 calls/day**. The budget
  tracker enforces both caps in Redis (survives restarts).
* Every public fetcher checks ``can_afford_call()`` BEFORE the HTTP call and
  calls ``record_call()`` after — a call that would exceed the daily or
  lifetime budget is never made.
* Redis caching is the value lever: tweet results cached 15 min, user info
  1 hour, trends 30 min — a cached hit costs ZERO calls. Redis down →
  fail-open (no cache, straight to the API).
* Fail-open everywhere: no key, budget exhausted, rate-limited, API error, or
  malformed payload → ``[]`` / ``None`` with a debug log. A dead GetXAPI never
  breaks a tick and never raises into the scheduler.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from rugmunchbot.tools.getxapi_budget import can_afford_call, record_call
from rugmunchbot.tools.getxapi_config import (
    GETXAPI_BASE_URL,
    GETXAPI_COST_PER_CALL,
    GETXAPI_DAILY_CALL_LIMIT,
    GETXAPI_TOTAL_BUDGET,
    api_key_configured,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# CONFIG (env-overridable, all optional)
# ══════════════════════════════════════════════════════════════════════════

#: Per-request timeout (seconds).
GETXAPI_TIMEOUT_S = float(os.getenv("GETXAPI_TIMEOUT", "15"))
#: Redis cache TTLs (seconds) — the value lever on a small budget.
GETXAPI_CACHE_TWEETS_S = float(os.getenv("GETXAPI_CACHE_TWEETS_S", "900"))  # 15 min
GETXAPI_CACHE_USER_INFO_S = float(os.getenv("GETXAPI_CACHE_USER_INFO_S", "3600"))  # 1 hour
GETXAPI_CACHE_TRENDS_S = float(os.getenv("GETXAPI_CACHE_TRENDS_S", "1800"))  # 30 min

# ══════════════════════════════════════════════════════════════════════════
# CALL-COUNT TRACKER — budget the $5 envelope (thin wrapper over the tracker)
# ══════════════════════════════════════════════════════════════════════════

#: In-memory call counter (resets on restart; the Redis budget tracker is the
#: durable source of truth across restarts).
_call_count: int = 0


def call_count() -> int:
    """Total GetXAPI calls made this process (in-memory)."""
    return _call_count


def budget_remaining() -> dict[str, Any]:
    """Budget dashboard: calls used / remaining against the $5 envelope.

    Delegates to the Redis-backed budget tracker (``getxapi_budget``) so the
    numbers survive restarts. Returns a dict with ``total_calls``,
    ``calls_remaining``, ``budget_calls``, ``spend`` and ``days_left`` (at the
    default 150 calls/day pace).
    """
    from rugmunchbot.tools.getxapi_budget import get_daily_usage

    usage = get_daily_usage()
    total = int(usage.get("calls") or _call_count)
    remaining = int(usage.get("calls_remaining") or 0)
    days_left = remaining / 150.0 if remaining > 0 else 0.0
    return {
        "total_calls": total,
        "calls_remaining": remaining,
        "budget_calls": GETXAPI_DAILY_CALL_LIMIT,
        "spend": float(usage.get("spend") or 0.0),
        "total_budget": GETXAPI_TOTAL_BUDGET,
        "cost_per_call": GETXAPI_COST_PER_CALL,
        "days_left": round(days_left, 1),
    }


def _note_call(endpoint: str = "") -> None:
    """Increment the in-memory counter and meter the durable budget ledger."""
    global _call_count
    _call_count += 1
    record_call(endpoint=endpoint)


# ══════════════════════════════════════════════════════════════════════════
# REDIS CACHE — the value lever (cached hit = zero calls)
# ══════════════════════════════════════════════════════════════════════════


async def _cache_get(key: str) -> Any | None:
    """Read a JSON value from Redis; returns the parsed value or None.

    Fail-open: Redis down / missing / malformed → None (caller hits the API).
    """
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        client = get_redis_async()
        if client is None:
            return None
        raw = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:  # fail-open by contract
        logger.debug("getxapi:cache_read_failed key=%s", key)
        return None


async def _cache_set(key: str, value: Any, ttl: float) -> None:
    """Write a JSON value to Redis with a TTL (fail-open)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        client = get_redis_async()
        if client is None:
            return
        await client.set(key, json.dumps(value), ex=int(ttl))
    except Exception:  # fail-open by contract
        logger.debug("getxapi:cache_write_failed key=%s", key)


def _cache_key(prefix: str, *parts: str) -> str:
    """Build a namespaced Redis cache key."""
    joined = ":".join(str(p).strip().lower() for p in parts if str(p).strip())
    return f"getxapi:{prefix}:{joined}"


# ══════════════════════════════════════════════════════════════════════════
# HTTP CORE — one request helper, budget-gated, fail-open
# ══════════════════════════════════════════════════════════════════════════


def _auth_headers() -> dict[str, str]:
    from rugmunchbot.tools.getxapi_config import _resolve_api_key

    key = _resolve_api_key()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


async def _get(path: str, params: dict[str, Any] | None = None) -> Any | None:
    """One authenticated GET to the GetXAPI base URL; returns parsed JSON.

    Budget-gated: checks ``can_afford_call()`` BEFORE the HTTP call and
    meters via ``record_call()`` after. Fail-open: no key / budget exhausted /
    HTTP error / timeout / malformed JSON → None. A cached hit never reaches
    here.
    """
    if not api_key_configured():
        logger.debug("getxapi:get_skipped_no_key path=%s", path)
        return None
    if not can_afford_call():
        logger.debug("getxapi:get_skipped_budget path=%s", path)
        return None
    try:
        async with httpx.AsyncClient(timeout=GETXAPI_TIMEOUT_S) as client:
            r = await client.get(
                f"{GETXAPI_BASE_URL}{path}",
                params=params,
                headers=_auth_headers(),
            )
            _note_call(endpoint=path)
            if r.status_code != 200:
                logger.warning(
                    "getxapi:http status=%s path=%s err=%s",
                    r.status_code,
                    path,
                    r.text[:160],
                )
                return None
            return r.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.warning("getxapi:get_failed path=%s err=%s", path, str(exc)[:160])
        return None


# ══════════════════════════════════════════════════════════════════════════
# NORMALIZATION — hostile/messy provider payloads → one clean tweet shape
# ══════════════════════════════════════════════════════════════════════════


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce a payload value to a dict (non-dicts become {} — fail-open)."""
    return value if isinstance(value, dict) else {}


def _tweet_url(handle: str, tweet_id: str) -> str:
    h = str(handle or "").strip().lstrip("@")
    if h and tweet_id:
        return f"https://x.com/{h}/status/{tweet_id}"
    return f"https://x.com/i/status/{tweet_id}" if tweet_id else ""


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """First non-None value among *keys* (tolerant to provider key drift)."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return default


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_tweet(raw: Any) -> dict[str, Any] | None:
    """Normalize one raw GetXAPI tweet payload into the shared tweet shape.

    Tolerates the flat ``{id, text, ...}`` shape and a nested ``tweet`` /
    ``user`` object. Returns None for payloads without a tweet id + text
    (hostile rows skipped). The output shape matches ``apipass_x.normalize_tweet``
    so downstream consumers (``x_ingest.tweet_to_article``) work unchanged.
    """
    if not isinstance(raw, dict):
        return None
    nested = raw.get("tweet")
    tweet: dict[str, Any] = nested if isinstance(nested, dict) else raw
    if not isinstance(tweet, dict):
        return None
    tweet_id = str(_first(tweet, "id", "tweet_id", "id_str", "rest_id", default="") or "").strip()
    text = str(
        _first(tweet, "text", "full_text", "tweet_text", "content", default="") or ""
    ).strip()
    if not text or not tweet_id:
        return None

    author = _as_dict(tweet.get("author"))
    user = _as_dict(tweet.get("user"))
    handle = (
        str(
            _first(author, "username", "screen_name", "handle", default="")
            or _first(user, "username", "screen_name", "handle", default="")
            or _first(tweet, "username", "screen_name", "handle", default="")
        )
        .strip()
        .lstrip("@")
    )
    author_name = (
        str(
            _first(author, "name", "display_name", "full_name", default="")
            or _first(user, "name", "display_name", default="")
            or _first(tweet, "name", "display_name", default="")
        ).strip()
        or handle
    )

    created = str(_first(tweet, "created_at", "createdAt", "timestamp", default="") or "").strip()
    metrics = tweet.get("engagement") if isinstance(tweet.get("engagement"), dict) else {}
    if not metrics and isinstance(tweet.get("public_metrics"), dict):
        metrics = tweet["public_metrics"]
    if not metrics and isinstance(tweet.get("metrics"), dict):
        metrics = tweet["metrics"]
    if not metrics:
        metrics = {
            "retweet_count": _first(tweet, "retweet_count", "retweets", default=0),
            "like_count": _first(tweet, "like_count", "likes", "favorite_count", default=0),
            "reply_count": _first(tweet, "reply_count", "replies", default=0),
            "quote_count": _first(tweet, "quote_count", "quotes", default=0),
        }

    return {
        "tweet_id": tweet_id,
        "text": text[:500],
        "handle": handle,
        "author_name": author_name,
        "created_at": created,
        "retweet_count": _as_int(_first(metrics, "retweet_count", "retweets", "retweetCount")),
        "like_count": _as_int(
            _first(metrics, "like_count", "likes", "likeCount", "favorite_count")
        ),
        "reply_count": _as_int(_first(metrics, "reply_count", "replies", "replyCount")),
        "quote_count": _as_int(_first(metrics, "quote_count", "quotes")),
        "url": _tweet_url(handle, tweet_id),
        "source": "getxapi",
    }


def _extract_tweets(payload: Any) -> list[dict[str, Any]]:
    """Pull every tweet-shaped dict out of a GetXAPI payload (tolerant).

    Walks common container keys (``data``, ``result``, ``tweets``, ``list``,
    ``items``, ``rows``, ``posts``) and list-valued keys; dedupes by tweet id.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            norm = normalize_tweet(value)
            if norm is not None:
                tid = norm["tweet_id"]
                if tid not in seen:
                    seen.add(tid)
                    out.append(norm)
                return
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for v in value:
                _walk(v)

    _walk(payload)
    return out


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC FETCHERS — each returns parsed JSON, budget-gated, Redis-cached
# ══════════════════════════════════════════════════════════════════════════


async def search_tweets(query: str, count: int = 20) -> list[dict[str, Any]]:
    """Search tweets by keyword (GET /tweet/advanced_search).

    Cached 15 min. Fail-open ``[]``. Returns normalized tweet dicts.
    """
    query = str(query or "").strip()
    if not query:
        return []
    cache_key = _cache_key("search", query, str(count))
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = await _get(
        "/tweet/advanced_search", {"query": query, "count": max(1, min(int(count), 100))}
    )
    tweets = _extract_tweets(payload)[: max(1, min(int(count), 100))]
    await _cache_set(cache_key, tweets, GETXAPI_CACHE_TWEETS_S)
    return tweets


async def get_user_tweets(username: str, count: int = 20) -> list[dict[str, Any]]:
    """Recent tweets from one X account (GET /user/tweets).

    Cached 15 min. Fail-open ``[]``. Returns normalized tweet dicts.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        return []
    cache_key = _cache_key("user_tweets", username, str(count))
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = await _get(
        "/user/tweets", {"username": username, "count": max(1, min(int(count), 100))}
    )
    tweets = _extract_tweets(payload)[: max(1, min(int(count), 100))]
    await _cache_set(cache_key, tweets, GETXAPI_CACHE_TWEETS_S)
    return tweets


async def get_user_info(username: str) -> dict[str, Any] | None:
    """Full profile for one X account (GET /user/info).

    Cached 1 hour. Fail-open ``None``.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        return None
    cache_key = _cache_key("user_info", username)
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = await _get("/user/info", {"username": username})
    info = payload if isinstance(payload, dict) else None
    if info is not None:
        await _cache_set(cache_key, info, GETXAPI_CACHE_USER_INFO_S)
    return info


async def get_user_mentions(username: str, count: int = 20) -> list[dict[str, Any]]:
    """Tweets mentioning one X account (GET /user/mentions).

    Cached 15 min. Fail-open ``[]``. Returns normalized tweet dicts.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        return []
    cache_key = _cache_key("user_mentions", username, str(count))
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = await _get(
        "/user/mentions", {"username": username, "count": max(1, min(int(count), 100))}
    )
    tweets = _extract_tweets(payload)[: max(1, min(int(count), 100))]
    await _cache_set(cache_key, tweets, GETXAPI_CACHE_TWEETS_S)
    return tweets


async def get_trends() -> list[dict[str, Any]]:
    """Current X trends (GET /trends).

    Cached 30 min. Fail-open ``[]``.
    """
    cache_key = _cache_key("trends")
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached
    payload = await _get("/trends")
    trends = payload if isinstance(payload, list) else []
    if not trends and isinstance(payload, dict):
        # Some providers wrap trends under a container key.
        for key in ("data", "trends", "result"):
            if isinstance(payload.get(key), list):
                trends = payload[key]
                break
    await _cache_set(cache_key, trends, GETXAPI_CACHE_TRENDS_S)
    return trends


async def get_user_status(username: str) -> dict[str, Any] | None:
    """Account status for one X account (GET /user/status).

    Fail-open ``None``.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        return None
    payload = await _get("/user/status", {"username": username})
    return payload if isinstance(payload, dict) else None


__all__ = [
    "GETXAPI_BASE_URL",
    "GETXAPI_DAILY_CALL_LIMIT",
    "GETXAPI_TOTAL_BUDGET",
    "api_key_configured",
    "budget_remaining",
    "call_count",
    "get_trends",
    "get_user_info",
    "get_user_mentions",
    "get_user_status",
    "get_user_tweets",
    "normalize_tweet",
    "search_tweets",
]
