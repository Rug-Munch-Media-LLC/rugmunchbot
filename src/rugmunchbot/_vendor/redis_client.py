# VENDORED 2026-08-17 from rmi-backend/app/core/redis.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Redis singleton - single source of truth for Redis connections.

Win #14 degradation: if Redis is down, the in-process LRU cache serves
stale data. Callers check get_redis_degraded() to detect degraded mode.

CONSOLIDATION (2026-07-24): Now uses ``app.config.settings``
instead of direct os.getenv(). Falls back to os.getenv during early
bootstrapping before settings is fully initialized.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    import redis
    import redis.asyncio as aioredis


def _get_redis_url() -> str:
    """Get Redis URL from settings, falling back to direct env var during boot.
    If the URL has no password, try fetching from gopass (bot runs on host)."""
    url = ""
    try:
        from rugmunchbot._vendor.settings_shim import settings as _s

        if _s.redis_url:
            url = _s.redis_url
    except Exception:  # noqa: S110 — optional dependency
        pass
    if not url:
        url = os.getenv("REDIS_URL", "")
    if not url:
        url = "redis://localhost:6379/0"
    # If URL has no password, fetch from the secrets manager (path via env).
    if "@" not in url:
        try:
            pw = (
                subprocess.check_output(  # noqa: S603 — fixed binary, operator-controlled env arg, shell=False
                    ["/usr/bin/gopass", "show", os.environ.get("REDIS_PASSWORD_GOPASS_PATH", "")],
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    shell=False,
                )
                .decode()
                .strip()
            )
            if pw:
                url = url.replace("redis://", f"redis://:{pw}@")
                # Normalize hostname for host-based processes
                url = url.replace("@rmi-redis:", "@localhost:")
        except Exception:  # noqa: S110 — gopass may not be installed
            pass
    return url


REDIS_URL = _get_redis_url()

_redis_client: redis.Redis | aioredis.Redis | None = None
_sync_pool: redis.ConnectionPool | None = None
_async_pool: aioredis.ConnectionPool | None = None
_degraded: bool = False


def get_redis() -> redis.Redis | None:
    """Get Redis client with connection pooling. Single source of truth."""
    global _redis_client, _sync_pool, _degraded
    import redis

    if _sync_pool is None:
        try:
            _sync_pool = redis.ConnectionPool.from_url(
                REDIS_URL,
                max_connections=20,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            logger.info("Redis connection pool created (max 20)")
        except Exception as e:
            logger.error(f"Redis pool creation failed: {e}")
            _mark_degraded()
            return None

    try:
        _redis_client = redis.Redis(connection_pool=_sync_pool)
        _redis_client.ping()
        if _degraded:
            _mark_healthy()
        return _redis_client
    except Exception as e:
        logger.error(f"Redis client creation failed: {e}")
        _mark_degraded()
        return None


def get_redis_async() -> aioredis.Redis | None:
    """Get async Redis client for async operations."""
    global _redis_client, _async_pool
    import redis.asyncio as aioredis

    if _async_pool is None:
        try:
            _async_pool = aioredis.ConnectionPool.from_url(
                REDIS_URL,
                max_connections=20,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            logger.info("Async Redis connection pool created (max 20)")
        except Exception as e:
            logger.error(f"Async Redis pool creation failed: {e}")
            return None

    try:
        _redis_client = aioredis.Redis(connection_pool=_async_pool)
        return _redis_client
    except Exception as e:
        logger.error(f"Async Redis client creation failed: {e}")
        return None


def close_redis() -> None:
    """Close Redis connection pool."""
    global _sync_pool, _async_pool
    if _sync_pool:
        try:
            _sync_pool.disconnect()
            logger.info("Redis connection pool closed")
        except Exception as e:
            logger.error(f"Redis pool close failed: {e}")
        _sync_pool = None
    if _async_pool:
        try:
            _async_pool.disconnect()  # type: ignore[unused-coroutine]
            logger.info("Async Redis connection pool closed")
        except Exception as e:
            logger.error(f"Async Redis pool close failed: {e}")
        _async_pool = None


def _mark_degraded() -> None:
    """Mark Redis as degraded in the global registry."""
    global _degraded
    _degraded = True
    try:
        from rugmunchbot._vendor.degradation import get_registry

        get_registry().mark_down("redis")
    except (AttributeError, ImportError):
        pass


def _mark_healthy() -> None:
    """Mark Redis as healthy in the global registry."""
    global _degraded
    _degraded = False
    try:
        from rugmunchbot._vendor.degradation import get_registry

        get_registry().mark_healthy("redis")
    except (AttributeError, ImportError):
        pass


def is_redis_degraded() -> bool:
    """Check if Redis is currently degraded."""
    return _degraded


def get_lru_cache() -> Any | None:
    """Get the in-process LRU cache for degraded operation."""
    try:
        from rugmunchbot._vendor.degradation import get_registry

        return get_registry().lru
    except ImportError:
        return None
