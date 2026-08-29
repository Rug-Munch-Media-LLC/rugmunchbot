"""ApiPass X (Twitter) data source — real tweet intel at ~0.5 credit/call.

Why this exists
---------------
The bot's social rails previously ran on SearXNG meta-search (web pages that
*happen to mention* a token) — no real tweet payloads, no engagement, no
author attribution, and no way to track KOL calls or deleted tweets. ApiPass
(``api.apipass.dev``) fronts the X API with one key (``apk_...``) and a
credit meter (~0.5 credit per call, 50 free credits on the account). This
module is the first-party source that turns ApiPass into KOL/shill/CT-intel
data: create a task → poll it → normalize the tweet payload → embed → dedup →
rerank → hand the ranked tweets to channels and the site.

Budget doctrine (hard, enforced here)
-------------------------------------
* ≤ ``RMI_APIPASS_MAX_CALLS_PER_HOUR`` task creations per hour (default 3).
  Polling ``recordInfo`` is free and does NOT count against the burn.
* Fail-open everywhere: no key, rate-limited, API error, or malformed
  payload → ``None`` / ``[]`` with a debug log. A dead ApiPass never breaks
  a channel tick — callers fall back to the existing SearXNG rail.
* The key never enters git. Resolution order: env ``APIPASS_API_KEY`` →
  gopass ``rmi/apipass/api_key`` (same gopass→env ladder the rest of the
  bot uses). Never logged.

ApiPass task flow (OpenAPI-verified 2026-08-29)
-----------------------------------------------
1. ``POST /api/v1/jobs/createTask``  body ``{model, input}``,
   header ``Authorization: Bearer <key>`` → ``{data: {taskId}}``
2. ``GET  /api/v1/jobs/recordInfo?taskId=<id>`` → ``{data: {state,
   resultJson, failMsg, ...}}`` — poll until a terminal state.

Model: ``twitter-api-simple`` — endpoints ``tweet-detail``, ``user-tweets``,
``search``, ``quotes``, ``retweeters`` (verified via ``/v1/models``).
``input`` is ``{"endpoint": ..., "params": {...}}`` with the params also
flattened at the top level, so either provider-side convention works.

Post-fetch pipeline (the "embed → dedup → rerank" contract):
* ``semantic_dedup`` — qwen3-4B embed (``_vendor.rag_pkg.embeddings``) +
  cosine threshold against a recent-vector store, so the same shill claim
  phrased differently is caught. Fail-open: embed down → URL dedup only.
* ``rerank_tweets`` — qwen3 reranker (``_vendor.rag_pkg.rerank``) ranks the
  candidates against a caller query; fail-open keeps input order.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import time
from collections import deque
from typing import Any

import httpx

from rugmunchbot._vendor.rag_pkg.embeddings import get_embedding
from rugmunchbot._vendor.rag_pkg.rerank import rerank_results

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# CONFIG (env-overridable, all optional)
# ══════════════════════════════════════════════════════════════════════════

APIPASS_BASE_URL = os.getenv("APIPASS_BASE_URL", "https://api.apipass.dev").rstrip("/")
#: ApiPass model slug that fronts the X API (verified in /v1/models).
APIPASS_TWITTER_MODEL = os.getenv("APIPASS_TWITTER_MODEL", "twitter-api-simple")
#: Hard budget — max task creations per hour (default 3, per the ≤3/hr burn).
MAX_CALLS_PER_HOUR = int(os.getenv("RMI_APIPASS_MAX_CALLS_PER_HOUR", "3"))
#: Per-request timeouts (seconds).
CREATE_TIMEOUT_S = float(os.getenv("APIPASS_CREATE_TIMEOUT", "15"))
POLL_TIMEOUT_S = float(os.getenv("APIPASS_POLL_TIMEOUT", "10"))
#: Poll cadence for a task (task completion is async server-side).
POLL_INTERVAL_S = float(os.getenv("APIPASS_POLL_INTERVAL", "2.5"))
POLL_TOTAL_S = float(os.getenv("APIPASS_POLL_TOTAL", "90"))
#: Semantic-dedup cosine threshold — below this the text is "new".
DEDUP_COSINE_THRESHOLD = float(os.getenv("APIPASS_DEDUP_COSINE", "0.90"))
#: Max recent vectors kept for semantic dedup (bounded memory).
DEDUP_MAX_VECTORS = int(os.getenv("APIPASS_DEDUP_MAX_VECTORS", "200"))

#: Canonical gopass path for the ApiPass key (never in git).
_GOPASS_PATH = "rmi/apipass/api_key"
_ENV_KEYS = ("APIPASS_API_KEY",)

# ══════════════════════════════════════════════════════════════════════════
# RATE LIMITER — ≤ MAX_CALLS_PER_HOUR task creations (in-memory window)
# ══════════════════════════════════════════════════════════════════════════
# In-memory by design (matches ``_posted_keys``): a restart resets the hour
# window, which only widens the allowance — never narrows it. Task *polling*
# is free and does not touch this limiter.

_task_created_at: deque[float] = deque(maxlen=MAX_CALLS_PER_HOUR or 1)


def _task_creates_this_hour() -> int:
    now = time.monotonic()
    while _task_created_at and now - _task_created_at[0] >= 3600.0:
        _task_created_at.popleft()
    return len(_task_created_at)


def task_creates_remaining() -> int:
    """Task-creation calls left in the current hour window (0..MAX)."""
    if MAX_CALLS_PER_HOUR <= 0:
        return 0
    return max(0, MAX_CALLS_PER_HOUR - _task_creates_this_hour())


def _task_create_allowed() -> bool:
    if MAX_CALLS_PER_HOUR <= 0:
        logger.debug("apipass_x:disabled_by_budget max_calls_per_hour=0")
        return False
    if _task_creates_this_hour() >= MAX_CALLS_PER_HOUR:
        logger.warning(
            "apipass_x:rate_limited used=%s max=%s",
            _task_creates_this_hour(),
            MAX_CALLS_PER_HOUR,
        )
        return False
    return True


def _note_task_created() -> None:
    _task_created_at.append(time.monotonic())


# ══════════════════════════════════════════════════════════════════════════
# KEY RESOLUTION — env → gopass, never logged, never in git
# ══════════════════════════════════════════════════════════════════════════


def _resolve_api_key() -> str:
    """ApiPass key: env ``APIPASS_API_KEY`` → gopass ``rmi/apipass/api_key``.

    UNCACHED so a key added to gopass while the bot runs is picked up on the
    next tick without a restart. Returns "" when neither is configured —
    callers fail open (SearXNG fallback rail).
    """
    for key in _ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    try:
        value = (
            subprocess.check_output(  # noqa: S603 — gopass is the canonical secret store
                ["gopass", "show", _GOPASS_PATH],  # noqa: S607 — fixed tool, arg is a secret name
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
            .decode()
            .strip()
        )
        if value:
            return value
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return ""


def api_key_configured() -> bool:
    """True when an ApiPass key is available (env or gopass)."""
    return bool(_resolve_api_key())


def _auth_headers() -> dict[str, str]:
    key = _resolve_api_key()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


# ══════════════════════════════════════════════════════════════════════════
# TASK API (create → poll)
# ══════════════════════════════════════════════════════════════════════════


def _build_input(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Input body for createTask: ``{endpoint, params}`` + flattened params.

    ApiPass's ``input`` schema is ``additionalProperties: true`` (OpenAPI
    verified), so carrying both conventions is safe and maximizes provider
    compatibility (some endpoints read ``params.username``, some read
    ``input.username``).
    """
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    return {"endpoint": endpoint, "params": clean, **clean}


async def create_task(model: str, input_body: dict[str, Any]) -> str | None:
    """Create one ApiPass task; returns ``taskId`` or None (fail-open).

    Applies the hourly budget BEFORE the HTTP call — a call that would
    exceed the burn is not made. Returns None when unconfigured, rate
    limited, or the API rejects the task.
    """
    if not _resolve_api_key():
        logger.debug("apipass_x:create_skipped_no_key")
        return None
    if not _task_create_allowed():
        return None
    payload = {"model": model, "input": input_body}
    try:
        async with httpx.AsyncClient(timeout=CREATE_TIMEOUT_S) as client:
            r = await client.post(
                f"{APIPASS_BASE_URL}/api/v1/jobs/createTask",
                json=payload,
                headers=_auth_headers(),
            )
            if r.status_code != 200:
                logger.warning(
                    "apipass_x:create_http status=%s err=%s", r.status_code, r.text[:160]
                )
                return None
            body = r.json() or {}
            data = body.get("data") or {}
            task_id = str(data.get("taskId") or "").strip()
            if not task_id:
                logger.warning("apipass_x:create_no_task_id body=%s", str(body)[:200])
                return None
            _note_task_created()
            logger.info("apipass_x:task_created model=%s task=%s", model, task_id)
            return task_id
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.warning("apipass_x:create_failed err=%s", str(exc)[:160])
        return None


async def get_task(task_id: str) -> dict[str, Any] | None:
    """Poll one task record; returns the ``data`` dict or None (fail-open)."""
    if not task_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT_S) as client:
            r = await client.get(
                f"{APIPASS_BASE_URL}/api/v1/jobs/recordInfo",
                params={"taskId": task_id},
                headers=_auth_headers(),
            )
            if r.status_code != 200:
                logger.debug("apipass_x:poll_http status=%s task=%s", r.status_code, task_id)
                return None
            body = r.json() or {}
            return body.get("data") if isinstance(body.get("data"), dict) else None
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.debug("apipass_x:poll_failed task=%s err=%s", task_id, str(exc)[:120])
        return None


_TERMINAL_STATES: frozenset[str] = frozenset(
    {"success", "succeeded", "completed", "complete", "done", "finished"}
)
_FAIL_STATES: frozenset[str] = frozenset({"failed", "failure", "error", "canceled", "cancelled"})


async def run_task(
    model: str, input_body: dict[str, Any], *, total_timeout: float | None = None
) -> dict[str, Any] | None:
    """Create + poll until terminal; returns ``resultJson`` dict or None.

    Polling is free (does not burn credits). A task that never reaches a
    terminal state within the total timeout is abandoned (fail-open) — the
    ApiPass credit for a failed task is refunded server-side, and the
    caller simply falls back to the next rail.
    """
    task_id = await create_task(model, input_body)
    if not task_id:
        return None
    budget_s = float(total_timeout or POLL_TOTAL_S)
    deadline = time.monotonic() + budget_s
    while True:
        await _sleep(POLL_INTERVAL_S)
        record = await get_task(task_id)
        if record is None:
            if time.monotonic() >= deadline:
                logger.warning("apipass_x:task_timeout task=%s", task_id)
                return None
            continue
        state = str(record.get("state") or "").strip().lower()
        if state in _FAIL_STATES:
            logger.warning(
                "apipass_x:task_failed task=%s code=%s msg=%s",
                task_id,
                record.get("failCode") or "",
                str(record.get("failMsg") or "")[:200],
            )
            return None
        if state in _TERMINAL_STATES:
            result = record.get("resultJson")
            return result if isinstance(result, dict) else None
        if time.monotonic() >= deadline:
            logger.warning("apipass_x:task_timeout task=%s state=%s", task_id, state)
            return None


async def _sleep(seconds: float) -> None:
    """Sleep without importing asyncio at module load (keeps import light)."""
    import asyncio

    await asyncio.sleep(seconds)


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
    """Normalize one raw tweet payload into the shared tweet shape.

    Tolerates the flat ``{tweet_id, text, ...}`` shape, an ``author`` /
    ``user`` nested object, and ``core.user_results`` style nesting. Returns
    None for payloads without a tweet id + text (hostile rows skipped).
    """
    if not isinstance(raw, dict):
        return None
    nested = raw.get("tweet")
    tweet: dict[str, Any] = nested if isinstance(nested, dict) else raw
    if not isinstance(tweet, dict):
        return None
    tweet_id = str(_first(tweet, "tweet_id", "id", "id_str", "rest_id", default="") or "").strip()
    text = str(
        _first(tweet, "text", "full_text", "tweet_text", "content", default="") or ""
    ).strip()
    if not text or not tweet_id:
        return None

    author = _as_dict(tweet.get("author"))
    user = _as_dict(tweet.get("user"))
    core_user = None
    core = tweet.get("core")
    if isinstance(core, dict):
        _ur = core.get("user_results")
        if isinstance(_ur, dict):
            core_user = _ur.get("result") if isinstance(_ur.get("result"), dict) else None
    legacy = None
    if isinstance(core_user, dict) and isinstance(core_user.get("legacy"), dict):
        legacy = core_user["legacy"]
    handle = (
        str(
            _first(author, "username", "screen_name", "handle", default="")
            or _first(user, "username", "screen_name", "handle", default="")
            or _first(tweet, "username", "screen_name", "handle", default="")
            or (str(legacy.get("screen_name") or "") if legacy else "")
        )
        .strip()
        .lstrip("@")
    )
    author_name = (
        str(
            _first(author, "name", "display_name", "full_name", default="")
            or _first(user, "name", "display_name", default="")
            or _first(tweet, "name", "display_name", default="")
            or (str(legacy.get("name") or "") if legacy else "")
        ).strip()
        or handle
    )

    created = str(_first(tweet, "created_at", "createdAt", "timestamp", default="") or "").strip()
    metrics = tweet.get("engagement") if isinstance(tweet.get("engagement"), dict) else {}
    if not metrics and isinstance(tweet.get("public_metrics"), dict):
        metrics = tweet["public_metrics"]
    if not metrics and isinstance(tweet.get("metrics"), dict):
        metrics = tweet["metrics"]
    # Flat payloads carry engagement at the tweet's top level — read those too.
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
        "source": "apipass_x",
    }


def _extract_tweets(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pull every tweet-shaped dict out of a provider resultJson (tolerant).

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
# EMBED → DEDUP (qwen3-4B semantic dedup, URL fallback)
# ══════════════════════════════════════════════════════════════════════════

#: Recent text → vector store for semantic dedup (bounded, fail-open).
_recent_vectors: deque[tuple[str, list[float]]] = deque(maxlen=DEDUP_MAX_VECTORS)
_embed_available: bool | None = None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _remember_vector(text: str, vector: list[float]) -> None:
    global _embed_available
    _recent_vectors.append((text, vector))
    _embed_available = True


def reset_embed_availability() -> None:
    """Force re-probe of the embedder (tests / after an outage)."""
    global _embed_available
    _embed_available = None


def _semantic_is_duplicate(text: str, vector: list[float]) -> bool:
    """Cosine-compare *vector* against recent vectors; True when near-dup."""
    for stored_text, stored_vector in _recent_vectors:
        if stored_text == text:
            return True
        if _cosine(vector, stored_vector) >= DEDUP_COSINE_THRESHOLD:
            return True
    return False


async def semantic_dedup(text: str, *, key: str = "") -> bool:
    """True when *text* is a semantic near-duplicate of a recent item.

    Embeds with qwen3-4B (``_vendor.rag_pkg.embeddings.get_embedding``) and
    compares cosine against the recent-vector window. When the embedder is
    unavailable the vector is not computed and this returns False (the
    caller's URL-keyed dedup still applies) — dedup degrades, never blocks.
    """
    global _embed_available
    if _embed_available is False:
        return False
    vector = await get_embedding(text)
    if not vector:
        _embed_available = False
        return False
    _embed_available = True
    if _semantic_is_duplicate(text, vector):
        return True
    _remember_vector(text, vector)
    return False


# ══════════════════════════════════════════════════════════════════════════
# RERANK (qwen3 reranker; fail-open keeps input order)
# ══════════════════════════════════════════════════════════════════════════


async def rerank_tweets(
    tweets: list[dict[str, Any]], query: str, top_n: int = 5
) -> list[dict[str, Any]]:
    """Rank *tweets* by relevance to *query* via the qwen3 reranker.

    Fail-open: reranker unavailable/empty → input order unchanged (the
    caller's own ordering still applies). Returns up to ``top_n`` tweets.
    """
    if not tweets:
        return []
    documents = [str(t.get("text") or "")[:500] for t in tweets]
    try:
        ranked = await rerank_results(
            query, documents, top_n=min(top_n, len(documents)), tier="live"
        )
    except Exception as exc:  # fail-open by contract
        logger.debug("apipass_x:rerank_failed err=%s", str(exc)[:120])
        return tweets[:top_n]
    if not ranked:
        return tweets[:top_n]
    ordered: list[dict[str, Any]] = []
    for item in sorted(ranked, key=lambda r: float(r.get("relevance_score") or 0.0), reverse=True):
        idx = int(item.get("index") or 0)
        if 0 <= idx < len(tweets):
            ordered.append(tweets[idx])
    return ordered[:top_n] or tweets[:top_n]


# ══════════════════════════════════════════════════════════════════════════
# PUBLIC FETCHERS
# ══════════════════════════════════════════════════════════════════════════


async def fetch_tweet_detail(tweet_id: str) -> dict[str, Any] | None:
    """One real tweet by id (the ApiPass 'tweet-detail' endpoint)."""
    tweet_id = str(tweet_id or "").strip()
    if not tweet_id:
        return None
    input_body = _build_input(
        "twitter/simple/tweet-detail",
        {"tweetId": tweet_id, "id": tweet_id, "url": _tweet_url("", tweet_id)},
    )
    result = await run_task(APIPASS_TWITTER_MODEL, input_body)
    tweets = _extract_tweets(result)
    return tweets[0] if tweets else None


async def fetch_user_tweets(handle: str, limit: int = 10) -> list[dict[str, Any]]:
    """Recent real tweets from one X account ('user-tweets' endpoint).

    Returns normalized tweet dicts (fail-open ``[]``). The caller owns
    semantic dedup / rerank via :func:`semantic_dedup` / :func:`rerank_tweets`.
    """
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return []
    input_body = _build_input(
        "twitter/simple/user-tweets",
        {"username": handle, "screenName": handle, "count": max(1, min(int(limit), 50))},
    )
    result = await run_task(APIPASS_TWITTER_MODEL, input_body)
    return _extract_tweets(result)[: max(1, min(int(limit), 50))]


async def search_tweets(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """X search results for *query* ('search' endpoint), newest-ish first.

    Fail-open ``[]``. Engaged tweets (likes + retweets) are kept; zero-
    engagement dust is dropped so the channel never posts dead shills.
    """
    query = str(query or "").strip()
    if not query:
        return []
    input_body = _build_input(
        "twitter/simple/search",
        {"query": query, "keyword": query, "count": max(1, min(int(limit) * 2, 50))},
    )
    result = await run_task(APIPASS_TWITTER_MODEL, input_body)
    tweets = _extract_tweets(result)
    tweets.sort(
        key=lambda t: int(t.get("like_count") or 0) * 2 + int(t.get("retweet_count") or 0),
        reverse=True,
    )
    return tweets[: max(1, min(int(limit), 50))]


__all__ = [
    "APIPASS_BASE_URL",
    "APIPASS_TWITTER_MODEL",
    "DEDUP_COSINE_THRESHOLD",
    "MAX_CALLS_PER_HOUR",
    "api_key_configured",
    "create_task",
    "fetch_tweet_detail",
    "fetch_user_tweets",
    "get_task",
    "normalize_tweet",
    "rerank_tweets",
    "reset_embed_availability",
    "run_task",
    "search_tweets",
    "semantic_dedup",
    "task_creates_remaining",
]
