# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_common.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Shared utilities for scanner engines — ClickHouse, Redis, caching, results.

Central home for the small helpers that every ``own_*`` / engine module used to
re-implement locally:

- ``run_sync`` — the async-to-thread worker for the sync ``clickhouse_driver``
  client (previously duplicated as ``_run_sync`` in 15 engine files).
- ``redis_get`` / ``redis_set`` — graceful JSON cache helpers on top of
  ``app.core.redis.get_redis_async`` (previously ``_redis_get`` / ``_redis_set``).
- ``get_cache_stats`` / ``clear_cache`` — standard cache-monitoring builders.
  Engines bind their own module-level cache dicts via a thin wrapper.
- ``unavailable_result`` / ``error_result`` — standard degraded/error shapes.
- ``iso_now`` / ``to_epoch`` / ``to_iso`` — datetime conversion helpers.
- ``_query_batch_labels`` / ``_query_contract_addresses`` — batch wallet_labels /
  dex_events lookups (moved from ``wallet_flow``).

Engines import these directly. Where existing unit tests patch the legacy
module-local names (``_redis_get`` / ``_redis_set``), the engine keeps a
one-line module-level alias so the patch target stays valid::

    _redis_get = redis_get
    _redis_set = redis_set

[REVIEW] The original task sketch proposed ``run_sync(ch_client, query, params)``
(ClickHouse-specific). Every engine call site uses the generic
``_run_sync(fn, *args, **kwargs)`` form (some pass non-query callables, e.g.
``price_oracle`` ``_ensure`` / ``_write`` / ``_read``), so the generic worker is
extracted instead and the sketch signature is intentionally not adopted.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError

from rugmunchbot._vendor.lite_chain_indexer import _get_ch_client
from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# Time-box for the shared batch lookups (seconds) — matches the per-engine
# ``_QUERY_TIMEOUT_SECONDS`` convention.
_QUERY_TIMEOUT_SECONDS = 10


# ═══════════════════════════════════════════════════════════════════════════
# ClickHouse
# ═══════════════════════════════════════════════════════════════════════════


async def run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a sync function in a thread pool (clickhouse_driver is sync).

    Generic worker shared by every scanner engine. Pass the callable (usually a
    closure that resolves the ClickHouse client) plus its arguments::

        rows = await run_sync(_query_wallet_transfers, wallet, chain, limit)
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# Redis
# ═══════════════════════════════════════════════════════════════════════════


async def redis_get(key: str) -> Any | None:
    """Fetch and parse a cached JSON payload from Redis (graceful on failure).

    Returns the parsed value (dict or list — whatever was stored) or ``None``
    on a miss or when Redis is unavailable.
    """
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is None:
            return None
        raw = await r.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("engine_redis_get_failed: %s", exc)
        return None


async def redis_set(key: str, value: Any, ttl: int = 300) -> bool:
    """Store a JSON-serialisable payload in Redis with TTL (graceful on failure)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is None:
            return False
        await r.setex(key, ttl, json.dumps(value, default=str))
        return True
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("engine_redis_set_failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Cache stats
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats(
    cache: dict[Any, Any] | None = None,
    cache_max: int | None = None,
    cache_ttl: int | None = None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build standard cache statistics for monitoring.

    Engines bind their own module-level cache via a thin wrapper::

        def get_cache_stats() -> dict[str, Any]:
            return _common.get_cache_stats(
                _HYPE_CACHE, _HYPE_CACHE_MAX, _HYPE_CACHE_TTL,
                extra={"clickhouse_connected": _get_ch_client() is not None},
            )
    """
    stats: dict[str, Any] = {}
    if cache is not None:
        stats["cache_size"] = len(cache)
    if cache_max is not None:
        stats["cache_max"] = cache_max
    if cache_ttl is not None:
        stats["cache_ttl_seconds"] = cache_ttl
    if extra:
        stats.update(extra)
    return stats


def clear_cache(*caches: dict[Any, Any]) -> None:
    """Clear one or more in-process caches (for testing / memory pressure)."""
    for cache in caches:
        cache.clear()


# ═══════════════════════════════════════════════════════════════════════════
# Results
# ═══════════════════════════════════════════════════════════════════════════


def unavailable_result(reason: str = "data_unavailable") -> dict[str, Any]:
    """Standard unavailable result."""
    return {"status": "unavailable", "reason": reason, "cached": False}


def error_result(error: str) -> dict[str, Any]:
    """Standard error result."""
    return {"status": "error", "error": error, "cached": False}


# ═══════════════════════════════════════════════════════════════════════════
# Datetime
# ═══════════════════════════════════════════════════════════════════════════


def iso_now() -> str:
    """ISO 8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def to_epoch(value: Any) -> float | None:
    """Convert a ClickHouse datetime (or anything with ``.timestamp()``) to epoch seconds."""
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except (ValueError, OSError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_iso(value: Any) -> str | None:
    """Convert a datetime to ISO-8601 string (or ``None``)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat())
        except (ValueError, TypeError):
            return str(value)
    return str(value) if value else None


# ═══════════════════════════════════════════════════════════════════════════
# Batch label / contract lookup (moved from wallet_flow)
# ═══════════════════════════════════════════════════════════════════════════


def _query_batch_labels(addresses: list[str]) -> list[tuple[Any, ...]]:
    """Batch wallet_labels lookup for a node list."""
    client = _get_ch_client()
    if client is None or not addresses:
        return []
    params: dict[str, Any] = {"addresses": tuple(addresses)}
    try:
        return client.execute(
            """
            SELECT address, label_name, label_category, label_subtype, source, is_sanctioned
            FROM wallet_memory.wallet_labels
            WHERE address IN %(addresses)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("engine_batch_labels_failed: %s", exc)
        return []


def _query_contract_addresses(addresses: list[str], chain: str) -> list[tuple[Any, ...]]:
    """Detect which node addresses appear as DEX contract addresses."""
    client = _get_ch_client()
    if client is None or not addresses:
        return []
    params: dict[str, Any] = {"addresses": tuple(addresses), "chain": chain}
    try:
        return client.execute(
            """
            SELECT DISTINCT contract_address
            FROM wallet_memory.dex_events
            WHERE chain = %(chain)s
              AND contract_address IN %(addresses)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("engine_contract_detect_failed: %s", exc)
        return []
