"""scheduler_state — synchronous durable dedup check (hot path, best-effort).

``_remember`` is sync and must check the durable store on EVERY call, so it
uses a dedicated short-timeout sync client. Fail-open: any failure returns
False (treat as not-posted) and the layer backs off for ``_SYNC_RETRY_SECONDS``.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot._vendor.redis_client import REDIS_URL
from rugmunchbot.support.logging import get_logger

from . import _config

logger = get_logger("rmi_bot.channels.state")

_UNSET = object()

_sync_client: Any | None = None
_sync_down_until = 0.0
_SYNC_RETRY_SECONDS = 60.0


def _sync_acquire() -> Any | None:
    """Return the cached sync Redis client, or None (down / backing off)."""
    global _sync_client, _sync_down_until
    if time.monotonic() < _sync_down_until:
        return None
    if _sync_client is None:
        try:
            import redis as _redis

            _sync_client = _redis.Redis.from_url(
                REDIS_URL,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
                retry_on_timeout=True,
            )
        except Exception as exc:  # fail-open by contract
            logger.debug("channels:state_sync_acquire_failed err=%s", str(exc)[:120])
            _sync_down_until = time.monotonic() + _SYNC_RETRY_SECONDS
            return None
    return _sync_client


def reset_sync() -> None:
    """Drop the cached sync client + backoff (tests / config reload)."""
    global _sync_client, _sync_down_until
    _sync_client = None
    _sync_down_until = 0.0


def has_posted(key: str, *, client: Any = _UNSET) -> bool:
    """Best-effort durable dedup check against the global posted-key set.

    Returns True ONLY when the key is durably recorded in Redis
    (``bot:channels:posted:__global__``). Any Redis failure returns False —
    the caller then falls through to the in-memory check (never blocks
    posting on a Redis outage).
    """
    global _sync_client, _sync_down_until
    r = _sync_acquire() if client is _UNSET else client
    if r is None:
        return False
    try:
        return bool(r.sismember(_config.GLOBAL_POSTED_SET, key))
    except Exception as exc:  # fail-open by contract
        _sync_client = None
        _sync_down_until = time.monotonic() + _SYNC_RETRY_SECONDS
        logger.debug("channels:state_has_posted_failed err=%s", str(exc)[:120])
        return False
