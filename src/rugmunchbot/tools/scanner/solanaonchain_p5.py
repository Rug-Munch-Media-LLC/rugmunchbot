"""_solana_onchain.py split part: the bounded TTL cache used by the resolvers."""

from __future__ import annotations

import time
from typing import Any

_CACHE_TTL_S = 120.0

_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    """Return the cached value for ``key``, or ``None`` when absent or stale."""
    hit = _cache.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.monotonic() - ts > _CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    """Store ``val`` for ``key``, clearing the cache when it grows unbounded."""
    if len(_cache) > 512:  # bounded
        _cache.clear()
    _cache[key] = (time.monotonic(), val)
