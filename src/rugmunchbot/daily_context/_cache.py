"""Daily market awareness Redis cache — fail-open 5-minute snapshot.

Extracted verbatim from the former single-module ``daily_context.py``.
"""

from __future__ import annotations

import json
from typing import Any

from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot.daily_context._constants import CACHE_KEY, CACHE_TTL
from rugmunchbot.daily_context._fetch import fetch_daily_data
from rugmunchbot.daily_context._fmt import _has_content
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.daily_context")


async def _cache_get() -> dict[str, Any] | None:
    r = get_redis_async()
    if r is None:
        return None
    try:
        raw = await r.get(CACHE_KEY)
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("daily_context cache read failed", exc_info=True)
        return None


async def _cache_set(data: dict[str, Any]) -> None:
    r = get_redis_async()
    if r is None:
        return
    try:
        await r.set(CACHE_KEY, json.dumps(data), ex=CACHE_TTL)
    except Exception:
        logger.debug("daily_context cache write failed", exc_info=True)


async def _load_daily_data() -> dict[str, Any]:
    """Redis-cached (5 min) daily data; refetches only on miss/empty."""
    cached = await _cache_get()
    if cached is not None and _has_content(cached):
        return cached
    data = await fetch_daily_data()
    # Only cache non-empty snapshots so a transient outage self-heals.
    if _has_content(data):
        await _cache_set(data)
    return data
