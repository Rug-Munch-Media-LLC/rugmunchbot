"""RugMunchBot — optional Redis shim (fail-open).

OSS replacement for the private ``app.core.redis``. Returns ``None`` when
Redis is not configured, so every caller's ``if r is not None`` guard keeps
working — the bot degrades to in-memory/SQLite state exactly as designed.

Config (all optional)::

    REDIS_URL       redis://... (wins)
    REDIS_HOST      default 127.0.0.1
    REDIS_PORT      default 6379
    REDIS_PASSWORD  optional
"""

from __future__ import annotations

import os
from typing import Any


def _redis_url() -> str:
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "") or None
    if password:
        return f"redis://:{password}@{host}:{port}"
    return f"redis://{host}:{port}"


def get_redis_async() -> Any | None:
    """Return an async Redis client, or ``None`` when Redis is unavailable.

    Fail-open: any import or connection problem returns ``None`` so callers
    degrade gracefully. The client is created lazily per call (callers
    already treat it as optional and re-probe after failures).
    """
    if not os.getenv("REDIS_URL", "").strip() and not os.getenv("REDIS_HOST", "").strip():
        # Not configured → no client, callers keep working in-memory.
        return None
    try:
        import redis.asyncio as aioredis

        return aioredis.from_url(_redis_url(), decode_responses=True)
    except Exception:  # pragma: no cover - defensive
        return None


__all__ = ["get_redis_async"]
