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
  gopass ``[INTERNAL_SECRET_PATH]`` (same gopass→env ladder the rest of the
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

Layout (modularized 2026-09-04 from the former single-module ``apipass_x.py``):
  * ``__init__``   — shared config, mutable state, patchable imports + re-exports
  * ``_config``    — key resolution, budget/rate limiter
  * ``_task``      — createTask → poll → run_task flow
  * ``_normalize`` — hostile provider payloads → one clean tweet/profile shape
  * ``_dedup``     — embed → semantic dedup + rerank
  * ``_fetch``     — public fetchers + persistent profile cache
"""

from __future__ import annotations

import base64  # noqa: F401  (re-exported for tests / callers)
import contextlib  # noqa: F401  (re-exported)
import hashlib  # noqa: F401  (re-exported)
import json  # noqa: F401  (re-exported)
import logging
import math  # noqa: F401  (re-exported)
import os
import subprocess  # noqa: F401  (re-exported; tests patch m.subprocess)
import tempfile
import time  # noqa: F401  (re-exported)
from collections import deque
from typing import Any

import httpx  # noqa: F401  (re-exported)

from rugmunchbot._vendor.rag_pkg.embeddings import (
    get_embedding,  # noqa: F401  (re-exported; tests patch m.get_embedding)
)
from rugmunchbot._vendor.rag_pkg.rerank import (
    rerank_results,  # noqa: F401  (re-exported; tests patch m.rerank_results)
)

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
_GOPASS_PATH = "[INTERNAL_SECRET_PATH]"
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

# ══════════════════════════════════════════════════════════════════════════
# EMBED → DEDUP state (qwen3-4B semantic dedup, URL fallback)
# ══════════════════════════════════════════════════════════════════════════

#: Recent text → vector store for semantic dedup (bounded, fail-open).
_recent_vectors: deque[tuple[str, list[float]]] = deque(maxlen=DEDUP_MAX_VECTORS)
_embed_available: bool | None = None
#: Monotonic deadline after which a failed embedder is re-probed. Without a
#: cooldown a single outage latches ``_embed_available=False`` for the whole
#: process and semantic dedup never recovers (silent rail loss).
_embed_retry_after: float = 0.0
_EMBED_RETRY_SECONDS: float = float(os.getenv("APIPASS_EMBED_RETRY_SECONDS", "300"))

# ══════════════════════════════════════════════════════════════════════════
# PROFILE CACHE state (disk-backed, fail-open)
# ══════════════════════════════════════════════════════════════════════════

_CACHE_OK = False
_CACHE_PROBED = False

# ══════════════════════════════════════════════════════════════════════════
# SUBMODULES — import after state/config so they can reference the package
# ══════════════════════════════════════════════════════════════════════════

from . import (  # noqa: E402,F401  (submodules re-exported)
    _config,
    _dedup,
    _fetch,
    _normalize,
    _task,
)
from ._config import (  # noqa: E402
    _auth_headers as _auth_headers,
    _meter_usage as _meter_usage,
    _note_task_created as _note_task_created,
    _resolve_api_key as _resolve_api_key,
    _task_create_allowed as _task_create_allowed,
    _task_creates_this_day as _task_creates_this_day,
    _task_creates_this_hour as _task_creates_this_hour,
    api_key_configured as api_key_configured,
    task_creates_remaining as task_creates_remaining,
)
from ._dedup import (  # noqa: E402
    _cosine as _cosine,
    _remember_vector as _remember_vector,
    _semantic_is_duplicate as _semantic_is_duplicate,
    rerank_tweets as rerank_tweets,
    reset_embed_availability as reset_embed_availability,
    semantic_dedup as semantic_dedup,
)
from ._fetch import (  # noqa: E402
    _cache_clear as _cache_clear,
    _cache_path as _cache_path,
    _cache_probe as _cache_probe,
    _cache_read as _cache_read,
    _cache_write as _cache_write,
    _find_user_node as _find_user_node,
    _resolve_user_id as _resolve_user_id,
    fetch_tweet_detail as fetch_tweet_detail,
    fetch_user_tweets as fetch_user_tweets,
    fetch_x_profile as fetch_x_profile,
    normalize_profile as normalize_profile,
    search_tweets as search_tweets,
)
from ._normalize import (  # noqa: E402
    _as_dict as _as_dict,
    _as_int as _as_int,
    _decode_user_id as _decode_user_id,
    _extract_tweets as _extract_tweets,
    _first as _first,
    _tweet_url as _tweet_url,
    normalize_tweet as normalize_tweet,
)
from ._task import (  # noqa: E402
    _FAIL_STATES as _FAIL_STATES,
    _TERMINAL_STATES as _TERMINAL_STATES,
    _build_input as _build_input,
    _sleep as _sleep,
    create_task as create_task,
    get_task as get_task,
    run_task as run_task,
)

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
