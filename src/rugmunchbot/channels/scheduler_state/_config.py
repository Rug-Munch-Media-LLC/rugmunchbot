"""scheduler_state — Redis keys, TTLs, retry policy (single source)."""

from __future__ import annotations

import os
from typing import Any

#: Redis key prefixes (shared namespace with scam_watch's bot:scam_watch:*).
POSTED_SET_PREFIX = "bot:channels:posted:"
LAST_RUN_HASH_KEY = "bot:channels:last_run"

#: Posted-key sets are bounded the same way the in-memory dedupe is
#: (``scheduler._MAX_POSTED``); only the most recent window is persisted.
#: 2026-09-02 dedup fix: raised 500 -> 5000 in lockstep with
#: ``scheduler._MAX_POSTED`` so the durable window covers ~2 weeks of posts.
POSTED_MAX = 5000

#: Global durable posted-key set. ``_remember`` checks this on EVERY call
#: (SISMEMBER, O(1)) so a key evicted from the in-memory window still
#: counts as posted. Bare keys only (lane prefixes stripped) — this is the
#: cross-channel durable dedupe.
GLOBAL_POSTED_SET = f"{POSTED_SET_PREFIX}__global__"


def env_ttl(name: str, default: int) -> int:
    """Positive int from ``name``, clamped to >=300s, else ``default``."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(300, int(raw.strip()))
    except (TypeError, ValueError):
        return default


#: How long posted-key entries survive in Redis (72h >> the longest interval
#: of 4h, so nothing re-posts across a restart within that window).
DEFAULT_TTL = env_ttl("RMI_CHANNELS_STATE_TTL_SECONDS", 72 * 3600)
LAST_RUN_TTL = 30 * 24 * 3600

#: Re-probe Redis after a failure no sooner than this (fail-open + self-heal).
RETRY_AFTER_SECONDS = 60.0


def to_str(value: Any) -> str:
    return value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)
