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
------------------------------------
* ≤ ``RMI_APIPASS_MAX_CALLS_PER_HOUR`` task creations per hour (default 5 — a
  modest governor so a small dollar envelope lasts; env-overridable).
  Polling ``recordInfo`` is free and does NOT count against the burn.
* "FEWER, RICHER pulls": ``fetch_x_profile`` returns the FULL X profile
  (profile + metrics + verification) in ONE createTask and is served from a
  disk-backed cache (``RMI_APIPASS_CACHE_DIR``) so a given screen_name is
  pulled once and reused — repeat lookups cost zero credits.
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

Model (corrected 2026-08-29, verified against the live model pages): the
``model`` field sent to createTask IS the full endpoint path —
``twitter/simple/user-tweets``, ``twitter/simple/tweet-detail``,
``twitter/simple/search``, ``twitter/simple/quotes``, ``twitter/simple/
retweeters``, plus the graphql family (``twitter/graphql/user-by-screen-name``,
etc.). ``input`` carries that endpoint's params directly (``user_id`` /
``tweet_id`` / ``q`` / ``screen_name``). The ``twitter-api-simple`` string is
NOT a callable model id — it is the brand/category wrapper ("social-media-api")
and returns ``404 "Model type not found or not configured"`` if sent as
``model``. There is no account-side "enable" step: the endpoints are live once
the account has credits; the correct fix was the model/input shape, not
provisioning.

Post-fetch pipeline (the "embed → dedup → rerank" contract):
* ``semantic_dedup`` — qwen3-4B embed (``_vendor.rag_pkg.embeddings``) +
  cosine threshold against a recent-vector store, so the same shill claim
  phrased differently is caught. Fail-open: embed down → URL dedup only.
* ``rerank_tweets`` — qwen3 reranker (``_vendor.rag_pkg.rerank``) ranks the
  candidates against a caller query; fail-open keeps input order.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import math
import os
import subprocess
import tempfile
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
# 2026-08-29 (verified against live ApiPass model pages): the ``model`` field
# sent to createTask IS the full endpoint path — ``twitter/simple/user-tweets``,
# ``twitter/simple/tweet-detail``, ``twitter/simple/search``, etc. There is no
# ``twitter-api-simple`` model id; that slug is the *brand/category* wrapper
# (social-media-api) and 404s. Kept as the env-overridable base prefix so
# operators can switch to the graphql family (``twitter/graphql``) if needed.
APIPASS_TWITTER_MODEL = os.getenv("APIPASS_TWITTER_MODEL", "twitter/simple")
#: Hard budget — max task creations per hour. Account dashboard (2026-08-29):
#: 15/hr, 50/day, 1,500 total. We run a MODEST governor (default 5/hr) so a
#: $9 envelope lasts — each createTask is a credit burn, polling is free.
#: "FEWER, richer pulls": the richer-pull fetchers below (``fetch_x_profile``
#: etc.) return the richest available payload per createTask and are served
#: from the persistent cache so the same screen_name is NEVER repulled.
MAX_CALLS_PER_HOUR = int(os.getenv("RMI_APIPASS_MAX_CALLS_PER_HOUR", "5"))
#: Daily task-creation cap (account limit 50/day) — soft, in-memory, resets on
#: restart like the hourly window (a restart only widens the allowance).
MAX_CALLS_PER_DAY = int(os.getenv("RMI_APIPASS_MAX_CALLS_PER_DAY", "40"))
#: Free-credit allowance + per-call cost (usage ledger / dashboard). The
#: social-media-api lane is ~0.1 credit/post (simple endpoints).
FREE_CREDITS = float(os.getenv("RMI_APIPASS_FREE_CREDITS", "50"))
CREDITS_PER_CALL = float(os.getenv("RMI_APIPASS_CREDITS_PER_CALL", "0.1"))
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

#: Persistent cache for screen_name → rich X profile. A profile barely moves
#: (handle, join date, bio); follower counts drift over hours but the *pull* is
#: what we pay for. Cache aggressively so a given screen_name is pulled ONCE
#: and reused — that is the value lever on a small budget. Default lives under
#: a generic writable temp dir (never an internal fleet path, per the
#: methodology-leak policy); override with ``RMI_APIPASS_CACHE_DIR``.
APIPASS_CACHE_DIR = os.getenv(
    "RMI_APIPASS_CACHE_DIR",
    os.path.join(tempfile.gettempdir(), "rmi", "apipass_cache"),
)
#: Profile cache TTL (seconds). Profiles are stable; 24h default keeps follower
#: drift fresh enough while cutting re-pulls to ~once/day.
APIPASS_PROFILE_CACHE_TTL = float(os.getenv("RMI_APIPASS_PROFILE_CACHE_TTL", "86400"))
#: How long a verbatim "unavailable/suspended" answer is cached (re-asking the
#: same suspended account every tick burns credits for nothing).
APIPASS_SUSPEND_CACHE_TTL = float(os.getenv("RMI_APIPASS_SUSPEND_CACHE_TTL", "604800"))

#: Canonical key file for the ApiPass key (never in git). Defaults to the
#: fleet secret-store location; overridable so tests/containers can point
#: elsewhere.
_KEY_FILE = os.getenv("RMI_APIPASS_KEY_FILE") or os.path.join(
    os.getenv("RMI_SECRETS_DIR", "/srv/secrets"), "apipass_api_key"
)
#: Canonical gopass path for the ApiPass key (never in git).
_GOPASS_PATH = "rmi/apipass/api_key"
_ENV_KEYS = ("APIPASS_API_KEY",)

# ══════════════════════════════════════════════════════════════════════════
# MODEL CATALOG — what ApiPass X (Twitter) endpoints exist
# ══════════════════════════════════════════════════════════════════════════
# The ApiPass ``model`` field IS the endpoint path. The platform's
# ``/api/v1/models`` metadata catalog only exposes its LLM lane (Claude etc.),
# NOT the X scrape endpoints — those are task-based createTask models. The set
# below is the discovery result: the ``simple`` family and the graphql
# ``user-by-screen-name`` endpoint are CONFIRMED against the live model pages
# + a live task this run. The remaining graphql ops mirror X's own GraphQL
# operations that ApiPass wraps; they are NOT re-verified this run → [REVIEW].
# Each entry: {"input": [...], "confirmed": bool, "note": str}.
MODEL_CATALOG: dict[str, dict[str, Any]] = {
    # ── graphql family (richest per-pull) ──
    "twitter/graphql/user-by-screen-name": {
        "input": ["variables.screen_name"],
        "confirmed": True,  # live-verified: returns rich profile (core/avatar/legacy)
        "note": "RICHEST single-pull: full profile + metrics + verification.",
    },
    "twitter/graphql/user-by-rest-id": {
        "input": ["variables.user_id"],
        "confirmed": False,  # [REVIEW] not re-verified this run
        "note": "Resolve by numeric id (profile, same rich shape).",
    },
    "twitter/graphql/user-tweets": {
        "input": ["variables.user_id", "variables.count"],
        "confirmed": False,  # [REVIEW]
        "note": "User media timeline (latest tweets + media). 2nd createTask per handle.",
    },
    "twitter/graphql/tweet-detail": {
        "input": ["variables.tweet_id"],
        "confirmed": False,  # [REVIEW]
        "note": "One tweet by id (graphql variant).",
    },
    "twitter/graphql/user-followers": {
        "input": ["variables.user_id"],
        "confirmed": False,  # [REVIEW]
        "note": "Followers list (user_id required).",
    },
    "twitter/graphql/user-following": {
        "input": ["variables.user_id"],
        "confirmed": False,  # [REVIEW]
        "note": "Following list (user_id required).",
    },
    # ── simple family (confirmed, cheaper ~0.1 credit) ──
    "twitter/simple/user-tweets": {
        "input": ["user_id", "count"],
        "confirmed": True,
        "note": "Recent user timeline (needs numeric user_id).",
    },
    "twitter/simple/tweet-detail": {
        "input": ["tweet_id"],
        "confirmed": True,
        "note": "One tweet by id.",
    },
    "twitter/simple/search": {
        "input": ["q", "count"],
        "confirmed": True,
        "note": "Keyword search.",
    },
    "twitter/simple/quotes": {
        "input": ["tweet_id"],
        "confirmed": True,
        "note": "Quotes of a tweet.",
    },
    "twitter/simple/retweeters": {
        "input": ["tweet_id"],
        "confirmed": True,
        "note": "Retweeters of a tweet.",
    },
}
#: The model id actually sent to createTask for the profile lane.
_MODEL_USER_BY_SCREEN_NAME = "twitter/graphql/user-by-screen-name"

# ══════════════════════════════════════════════════════════════════════════
# RATE LIMITER — ≤ MAX_CALLS_PER_HOUR + ≤ MAX_CALLS_PER_DAY (in-memory window)
# ══════════════════════════════════════════════════════════════════════════
# In-memory by design (matches ``_posted_keys``): a restart resets the windows,
# which only widens the allowance — never narrows it. Task *polling* is free
# and does not touch this limiter.

_task_created_at: deque[float] = deque(maxlen=MAX_CALLS_PER_HOUR or 1)
_day_created_at: deque[float] = deque(maxlen=MAX_CALLS_PER_DAY or 1)


def _task_creates_this_hour() -> int:
    now = time.monotonic()
    while _task_created_at and now - _task_created_at[0] >= 3600.0:
        _task_created_at.popleft()
    return len(_task_created_at)


def _task_creates_this_day() -> int:
    now = time.monotonic()
    while _day_created_at and now - _day_created_at[0] >= 86400.0:
        _day_created_at.popleft()
    return len(_day_created_at)


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
    if MAX_CALLS_PER_DAY > 0 and _task_creates_this_day() >= MAX_CALLS_PER_DAY:
        logger.warning(
            "apipass_x:rate_limited_day used=%s max=%s",
            _task_creates_this_day(),
            MAX_CALLS_PER_DAY,
        )
        return False
    return True


def _note_task_created() -> None:
    _task_created_at.append(time.monotonic())
    _day_created_at.append(time.monotonic())


def _meter_usage(endpoint: str = "", task_id: str = "") -> None:
    """Record one task creation in the credit ledger (fail-open).

    Writes to ``db.kol.record_apipass_usage`` so the 50-free-credit budget
    is tracked (see ``db.kol.apipass_usage_summary``). Never raises — a DB
    that is unavailable must not break the ApiPass rail.
    """
    try:
        from rugmunchbot.db import record_apipass_usage

        record_apipass_usage(
            endpoint=endpoint,
            model=APIPASS_TWITTER_MODEL,
            credits=CREDITS_PER_CALL,
            task_id=task_id,
            status="created",
        )
    except Exception:  # fail-open by contract
        logger.debug("apipass_x:meter_failed endpoint=%s", endpoint)


# ══════════════════════════════════════════════════════════════════════════
# KEY RESOLUTION — env → gopass, never logged, never in git
# ══════════════════════════════════════════════════════════════════════════


def _resolve_api_key() -> str:
    """ApiPass key: env ``APIPASS_API_KEY`` → key file
    ``$RMI_SECRETS_DIR/apipass_api_key`` → secrets manager.

    UNCACHED so a key added while the bot runs is picked up on the next tick
    without a restart. Returns "" when none is configured — callers fail
    open (SearXNG fallback rail).
    """
    for key in _ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    # Fleet secret-store file (world-readable secret dir, mode 0600).
    try:
        with open(_KEY_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    except (OSError, UnicodeDecodeError):
        pass
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
    """Input body for createTask — the endpoint's own params, top-level.

    2026-08-29 (verified against the live ApiPass model pages): the ``model``
    field IS the full endpoint path (``twitter/simple/user-tweets``,
    ``twitter/simple/tweet-detail``, ``twitter/simple/search``, …) and the
    ``input`` object carries the endpoint's params directly (``user_id`` /
    ``tweet_id`` / ``q``). The endpoint is NOT nested inside ``input`` — it
    rides in ``model`` (see ``run_task``). ``endpoint`` is kept as a param so
    the usage meter still logs the lane, but it is not sent in the payload.
    """
    del endpoint  # the endpoint path is passed as `model`, not inside `input`
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    return clean


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
            _meter_usage(endpoint=model, task_id=task_id)
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


def _decode_user_id(value: Any) -> str:
    """Coerce an X user identifier to a plain numeric id string.

    X GraphQL wraps user ids in a base64 relay id (``VXNlcjo5NzMyNjE0NzI=``
    == ``User:973261472``) on the ``id`` field; the numeric ``rest_id`` is the
    clean form. ``user-tweets`` needs the numeric id, so we normalize here:
    base64 ``User:N`` → ``N``, ``User:N`` → ``N``, plain numeric stays.
    Fail-open: anything else returns the stripped original (caller treats a
    non-numeric id as "resolution failed" and falls back to ``[]``).
    """
    s = str(value or "").strip()
    if not s:
        return ""
    if s.startswith("User:"):
        return s[len("User:") :].strip()
    # Relay id: base64("User:<id>")
    try:
        decoded = base64.b64decode(s).decode("utf-8")
        if decoded.startswith("User:"):
            return decoded[len("User:") :].strip()
    except ValueError:  # not base64 / not a relay id — leave as-is
        pass
    return s


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
#: Monotonic deadline after which a failed embedder is re-probed. Without a
#: cooldown a single outage latches ``_embed_available=False`` for the whole
#: process and semantic dedup never recovers (silent rail loss).
_embed_retry_after: float = 0.0
_EMBED_RETRY_SECONDS: float = float(os.getenv("APIPASS_EMBED_RETRY_SECONDS", "300"))


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
    global _embed_available, _embed_retry_after
    _embed_available = None
    _embed_retry_after = 0.0


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
    After an outage the embedder is re-probed on a cooldown
    (``APIPASS_EMBED_RETRY_SECONDS``) instead of being disabled forever, and
    the first failure is logged loudly.
    """
    global _embed_available, _embed_retry_after
    if _embed_available is False and time.monotonic() < _embed_retry_after:
        return False
    vector = await get_embedding(text)
    if not vector:
        if _embed_available is not False:
            logger.warning(
                "apipass_x:embed_unavailable semantic_dedup_disabled retry_seconds=%s",
                _EMBED_RETRY_SECONDS,
            )
        _embed_available = False
        _embed_retry_after = time.monotonic() + _EMBED_RETRY_SECONDS
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

# ── Persistent screen_name → rich-profile cache (disk-backed) ──────────────
# The value lever on a small budget: pull a profile once, reuse it. Cache is a
# per-handle JSON file under ``APIPASS_CACHE_DIR``; TTL is ``APIPASS_PROFILE_CACHE_TTL``
# (active) / ``APIPASS_SUSPEND_CACHE_TTL`` (unavailable). Fail-open: a cache
# read/write error never blocks a pull or a miss.

_CACHE_OK = False
_CACHE_PROBED = False


def _cache_path(handle: str) -> str:
    return os.path.join(
        APIPASS_CACHE_DIR, f"{hashlib.sha256(handle.lower().encode()).hexdigest()[:24]}.json"
    )


def _cache_probe() -> bool:
    """Once-per-process check that the cache dir is writable (fail-open)."""
    global _CACHE_OK, _CACHE_PROBED
    if _CACHE_PROBED:
        return _CACHE_OK
    _CACHE_PROBED = True
    try:
        os.makedirs(APIPASS_CACHE_DIR, exist_ok=True)
        _CACHE_OK = os.path.isdir(APIPASS_CACHE_DIR) and os.access(APIPASS_CACHE_DIR, os.W_OK)
    except OSError:
        _CACHE_OK = False
    return _CACHE_OK


def _cache_read(handle: str) -> dict[str, Any] | None:
    """Read a cached profile for *handle*; returns the stored dict or None.

    Expired entries (past their TTL) are treated as a miss and deleted. Returns
    a deep-copied dict so callers never mutate the cache in place.
    """
    if not _cache_probe():
        return None
    path = _cache_path(handle)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        ttl = float(data.get("ttl") or 0.0)
        fetched = float(data.get("fetched_at_ms") or 0.0)
        if ttl <= 0 or (time.time() * 1000.0 - fetched) > ttl * 1000.0:
            with contextlib.suppress(OSError):
                os.remove(path)
            return None
        return dict(data.get("profile") or {})
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _cache_write(handle: str, profile: dict[str, Any], ttl: float) -> bool:
    """Persist *profile* for *handle* with *ttl* seconds. Returns success."""
    if not _cache_probe():
        return False
    try:
        payload = {
            "handle": handle.lower().lstrip("@"),
            "fetched_at_ms": time.time() * 1000.0,
            "ttl": float(ttl),
            "profile": profile,
        }
        with open(_cache_path(handle), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        return True
    except OSError:
        return False


def _cache_clear(handle: str | None = None) -> int:
    """Evict cache entries (one handle or the whole cache). Returns count."""
    removed = 0
    if not _cache_probe():
        return 0
    try:
        if handle:
            p = _cache_path(handle)
            if os.path.isfile(p):
                os.remove(p)
                removed = 1
        else:
            for name in os.listdir(APIPASS_CACHE_DIR):
                if name.endswith(".json"):
                    try:
                        os.remove(os.path.join(APIPASS_CACHE_DIR, name))
                        removed += 1
                    except OSError:
                        pass
    except OSError:
        pass
    return removed


# ── Rich X-profile normalization (the richest single-pull contract) ────────
# ``twitter/graphql/user-by-screen-name`` returns the full user node. For an
# ACTIVE user the node carries ``core`` (name/screen_name/created_at),
# ``avatar`` (image), ``location``, ``verification``, ``is_blue_verified``,
# ``verification_info``, ``rest_id`` and ``legacy`` (description + all the
# counts). For an UNAVAILABLE account the node is ``UserUnavailable`` carrying
# verbatim ``reason``/``message`` (e.g. "User is suspended"). We normalize BOTH
# and never fabricate a field that the provider didn't send.


def _find_user_node(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Locate the user node in a graphql user-by-screen-name resultJson.

    Walks the known container shapes (``data.user.result`` and the generic
    containers the existing ``_resolve_user_id`` recognizes) and returns the
    dict that holds ``legacy``/``core``/``rest_id`` — or the ``UserUnavailable``
    node. Fail-open: returns None when no user node is found.
    """
    if not isinstance(payload, dict):
        return None

    def _candidate(v: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 6 or not isinstance(v, dict):
            return None
        if any(k in v for k in ("legacy", "core", "rest_id", "id_str")):
            return v
        if v.get("__typename") == "UserUnavailable" or "reason" in v or "message" in v:
            return v
        for key in ("result", "user", "data", "variables"):
            inner = v.get(key)
            if isinstance(inner, dict):
                found = _candidate(inner, depth + 1)
                if found:
                    return found
        for val in v.values():
            if isinstance(val, dict):
                found = _candidate(val, depth + 1)
                if found:
                    return found
        return None

    return _candidate(payload)


def normalize_profile(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a ``user-by-screen-name`` resultJson into the rich X profile.

    Returns a dict with ``available: bool``. Active profiles carry handle,
    user_id, name, join date, avatar/banner, bio, location, verification and a
    full ``metrics`` object. Unavailable accounts carry the verbatim
    ``reason``/``message``/``__typename`` and AVAILABLE=False (the caller caches
    this for ``APIPASS_SUSPEND_CACHE_TTL`` so we never re-spend credits asking
    about a banned account). Returns None when the payload has no user node.
    """
    node = _find_user_node(payload)
    if not isinstance(node, dict):
        return None
    # Unavailable / suspended / deleted.
    if node.get("__typename") == "UserUnavailable" or "reason" in node or "message" in node:
        return {
            "available": False,
            "reason": str(node.get("reason") or node.get("__typename") or ""),
            "message": str(node.get("message") or ""),
            "__typename": str(node.get("__typename") or ""),
        }
    core = _as_dict(node.get("core"))
    avatar = _as_dict(node.get("avatar"))
    loc = _as_dict(node.get("location"))
    ver = _as_dict(node.get("verification"))
    leg = _as_dict(node.get("legacy"))
    handle = str(core.get("screen_name") or "").strip().lstrip("@")
    if not handle:
        handle = str(node.get("screen_name") or node.get("username") or "").strip().lstrip("@")
    rest_id = _decode_user_id(node.get("rest_id") or node.get("id") or "")
    vinfo = _as_dict(node.get("verification_info"))
    vreason = _as_dict(vinfo.get("reason"))
    vdesc = _as_dict(vreason.get("description"))
    pinned = [str(x) for x in (leg.get("pinned_tweet_ids_str") or []) if x]
    return {
        "available": True,
        "handle": handle,
        "user_id": rest_id,
        "name": str(core.get("name") or handle),
        "screen_name": str(core.get("screen_name") or handle),
        "created_at": str(core.get("created_at") or ""),
        "avatar_url": str(avatar.get("image_url") or ""),
        "banner_url": str(leg.get("profile_banner_url") or ""),
        "bio": str(leg.get("description") or ""),
        "location": str(loc.get("location") or ""),
        "website": str(leg.get("url") or ""),
        "verified": bool(ver.get("verified") or False),
        "is_blue_verified": bool(node.get("is_blue_verified") or False),
        "verification_reason": str(vdesc.get("text") or ""),
        "metrics": {
            "followers": _as_int(leg.get("followers_count")),
            "following": _as_int(leg.get("friends_count")),
            "statuses": _as_int(leg.get("statuses_count")),
            "likes": _as_int(leg.get("favourites_count")),
            "listed": _as_int(leg.get("listed_count")),
            "media": _as_int(leg.get("media_count")),
            "fast_followers": _as_int(leg.get("fast_followers_count")),
            "normal_followers": _as_int(leg.get("normal_followers_count")),
        },
        "pinned_tweet_ids": pinned,
        "pinned_url": _tweet_url(handle, pinned[0]) if pinned else "",
        "url": f"https://x.com/{handle}",
        "source": "apipass_x",
    }


async def fetch_x_profile(handle: str, *, force: bool = False) -> dict[str, Any] | None:
    """RICH pull — one ApiPass createTask returns the full X profile + metrics.

    This is the "pull from X as much as we can each pull" contract. It is served
    from the persistent cache first (a handle is pulled once and reused), so
    repeat calls cost ZERO credits. ``force`` bypasses the cache. Fail-open:
    no key / rate limited / API error / malformed payload → None.
    """
    import asyncio

    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    if not _resolve_api_key():
        logger.debug("apipass_x:profile_skipped_no_key")
        return None
    if not force:
        cached = await asyncio.to_thread(_cache_read, handle)
        if cached is not None:
            logger.debug("apipass_x:profile_cache_hit handle=%s", handle)
            return dict(cached)
    try:
        result = await run_task(_MODEL_USER_BY_SCREEN_NAME, {"variables": {"screen_name": handle}})
    except Exception as exc:  # best-effort fetch, fail-open
        logger.debug("apipass_x:profile_fetch_failed handle=%s err=%s", handle, str(exc)[:120])
        return None
    profile = normalize_profile(result)
    if profile is None:
        logger.warning("apipass_x:profile_no_node handle=%s", handle)
        return None
    ttl = APIPASS_SUSPEND_CACHE_TTL if not profile.get("available") else APIPASS_PROFILE_CACHE_TTL
    await asyncio.to_thread(_cache_write, handle, profile, ttl)
    return profile


async def _resolve_user_id(handle: str) -> str | None:
    """Resolve an X screen name → numeric user id, reusing the cached profile.

    Callers pass handles, but ``twitter/simple/user-tweets`` requires a numeric
    ``user_id``. This now goes through :func:`fetch_x_profile` (the RICH single
    pull) so the profile is pulled once, cached, and the ``rest_id`` is read
    from cache on every subsequent call — the old code made a *separate*
    createTask per resolution, wasting half the budget. Fail-open: any error →
    None (caller returns ``[]``).
    """
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return None
    try:
        profile = await fetch_x_profile(handle)
    except Exception as exc:  # resolution is best-effort
        logger.debug("apipass_x:resolve_user_id_failed handle=%s err=%s", handle, str(exc)[:120])
        return None
    if profile and profile.get("available") and profile.get("user_id"):
        return str(profile["user_id"])
    return None


async def fetch_tweet_detail(tweet_id: str) -> dict[str, Any] | None:
    """One real tweet by id (the ApiPass 'tweet-detail' endpoint)."""
    tweet_id = str(tweet_id or "").strip()
    if not tweet_id:
        return None
    input_body = _build_input(
        "twitter/simple/tweet-detail",
        {"tweet_id": tweet_id},
    )
    result = await run_task(f"{APIPASS_TWITTER_MODEL}/tweet-detail", input_body)
    tweets = _extract_tweets(result)
    return tweets[0] if tweets else None


async def fetch_user_tweets(handle: str, limit: int = 10) -> list[dict[str, Any]]:
    """Recent real tweets from one X account ('user-tweets' endpoint).

    Returns normalized tweet dicts (fail-open ``[]``). The caller owns
    semantic dedup / rerank via :func:`semantic_dedup` / :func:`rerank_tweets`.

    2026-08-29: ``twitter/simple/user-tweets`` takes a numeric ``user_id``, not
    a handle. We only have a handle, so we first resolve handle → user_id via
    the graphql ``user-by-screen-name`` endpoint, then fetch the timeline.
    Fail-open at every hop (no id → ``[]``).
    """
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return []
    user_id = await _resolve_user_id(handle)
    if not user_id:
        return []
    input_body = _build_input(
        "twitter/simple/user-tweets",
        {"user_id": user_id, "count": max(1, min(int(limit), 50))},
    )
    result = await run_task(f"{APIPASS_TWITTER_MODEL}/user-tweets", input_body)
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
        {"q": query, "count": max(1, min(int(limit) * 2, 50))},
    )
    result = await run_task(f"{APIPASS_TWITTER_MODEL}/search", input_body)
    tweets = _extract_tweets(result)
    tweets.sort(
        key=lambda t: int(t.get("like_count") or 0) * 2 + int(t.get("retweet_count") or 0),
        reverse=True,
    )
    return tweets[: max(1, min(int(limit), 50))]


__all__ = [
    "APIPASS_BASE_URL",
    "APIPASS_CACHE_DIR",
    "APIPASS_TWITTER_MODEL",
    "CREDITS_PER_CALL",
    "DEDUP_COSINE_THRESHOLD",
    "FREE_CREDITS",
    "MAX_CALLS_PER_HOUR",
    "MODEL_CATALOG",
    "api_key_configured",
    "create_task",
    "fetch_tweet_detail",
    "fetch_user_tweets",
    "fetch_x_profile",
    "get_task",
    "normalize_profile",
    "normalize_tweet",
    "rerank_tweets",
    "reset_embed_availability",
    "run_task",
    "search_tweets",
    "semantic_dedup",
    "task_creates_remaining",
]
