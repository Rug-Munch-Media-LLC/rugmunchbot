# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_holder_age.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Own Holder Age Analysis — ClickHouse-Powered Holder Vintage Detection
=====================================================================
Phase 4 of the Data Ownership Strategy.

Analyzes when each holder first received the token to detect:
  - Fresh wallet farms (all or most holders are brand new)
  - Sudden influx of new holders (pump-farm / airdrop-farming pattern)
  - Stable holder bases (established, long-held distribution)

Architecture:
  - Queries evm_transfers for first-seen timestamp per holder
  - Queries deployer_events for token creation time (to normalize flags)
  - Returns an age distribution with detection flags
  - Falls back gracefully when data is insufficient

Usage:
    from rugmunchbot._vendor.scanner_engines.own_holder_age import get_own_holder_age

    result = await get_own_holder_age("0x...", "ethereum")
    # result = {
    #     "holder_count": 42,
    #     "token_age_hours": 168.5,
    #     "distribution": {
    #         "fresh_24h": 5, "fresh_24h_pct": 11.9,
    #         "fresh_7d": 12, "fresh_7d_pct": 28.6,
    #         "fresh_30d": 20, "fresh_30d_pct": 47.6,
    #         "established": 22, "established_pct": 52.4,
    #     },
    #     "median_holder_age_hours": 96.0,
    #     "recent_holders": [{"address": "0x...", "first_seen": "...", "age_hours": 1.2}],
    #     "flags": ["sudden_influx"],
    #     "confidence": 0.8,
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.evm_transfers (transfer events)
  Primary: wallet_memory.deployer_events (token creation time)
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_HOLDER_AGE_CACHE: dict[str, dict[str, Any]] = {}
_HOLDER_AGE_CACHE_TTL = 600  # 10 minutes — holder age changes slowly
_HOLDER_AGE_CACHE_MAX = 100

# ── Lazy ClickHouse client ────────────────────────────────────────────────
# Thread-local client cache. clickhouse_driver.Client rejects concurrent
# queries on one connection ("Simultaneous queries on single connection
# detected"), and this engine fans queries out across ``run_sync`` worker
# threads via asyncio.gather — one client per worker thread keeps the
# connection sequential-safe while still reusing connections.
_ch_local = threading.local()

# ── Chain normalization ───────────────────────────────────────────────────
_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client for wallet_memory database."""
    cached = getattr(_ch_local, "client", None)
    if cached is not None:
        return cached

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        candidate = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        candidate.execute("SELECT 1")
        logger.info("Own holder age ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        _ch_local.client = candidate
        return candidate
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own holder age ClickHouse unavailable: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_holder_age(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Analyze holder age distribution from own ClickHouse data.

    Computes when each holder first received the token and buckets holders
    into fresh_24h / fresh_7d / fresh_30d / established cohorts. Detection
    flags are normalized against token age so new tokens are not falsely
    flagged as wallet farms.

    Args:
        token_address: Token contract address
        chain: Chain name (ethereum, bsc, etc.)
        force_fresh: Bypass cache

    Returns:
        Dict with holder age distribution, median age, recent holders, and
        detection flags, or an error dict when insufficient data.
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain}"
    if not force_fresh and cache_key in _HOLDER_AGE_CACHE:
        cached = _HOLDER_AGE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _HOLDER_AGE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "holder_age",
        "result": "scanned",
        "source": "own",
        "holder_count": 0,
        "token_age_hours": None,
        "distribution": {
            "fresh_24h": 0,
            "fresh_24h_pct": 0.0,
            "fresh_7d": 0,
            "fresh_7d_pct": 0.0,
            "fresh_30d": 0,
            "fresh_30d_pct": 0.0,
            "established": 0,
            "established_pct": 0.0,
        },
        "median_holder_age_hours": None,
        "recent_holders": [],
        "flags": [],
        "confidence": 0.0,
    }

    try:
        # ── 1. Parallel: distribution buckets + token creation time ──
        buckets_task = asyncio.create_task(run_sync(_query_holder_age_buckets, token_address, chain))
        token_age_task = asyncio.create_task(run_sync(_query_token_age, token_address, chain))
        recent_task = asyncio.create_task(run_sync(_query_recent_holders, token_address, chain))

        buckets, token_age, recent_holders = await asyncio.gather(
            buckets_task,
            token_age_task,
            recent_task,
        )
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("holder_age_query_failed for %s: %s", token_address[:16], exc)
        result["result"] = "unavailable"
        return result

    if not buckets or buckets.get("total_holders", 0) == 0:
        logger.debug("no holder data for %s", token_address[:16])
        result["result"] = "unavailable"
        return result

    # ── 2. Populate distribution ──
    total = buckets["total_holders"]
    result["holder_count"] = total
    dist = result["distribution"]
    for key in ("fresh_24h", "fresh_7d", "fresh_30d", "established"):
        dist[key] = int(buckets.get(key, 0))
        dist[f"{key}_pct"] = round((dist[key] / total) * 100, 1)
    if buckets.get("median_age_hours") is not None:
        result["median_holder_age_hours"] = round(buckets["median_age_hours"], 1)
    result["token_age_hours"] = token_age.get("age_hours") if token_age else None
    result["recent_holders"] = recent_holders or []
    result["confidence"] = round(min(1.0, 0.5 + 0.1 * min(total / 50, 1.0)), 2)

    # ── 3. Detection flags (normalized by token age) ──
    token_age_h = result["token_age_hours"]
    fresh_24h_pct = dist["fresh_24h_pct"]
    fresh_7d_pct = dist["fresh_7d_pct"]
    established_pct = dist["established_pct"]

    if token_age_h is None or token_age_h > 48:
        if total > 20 and fresh_24h_pct >= 30:
            result["flags"].append("sudden_influx")
        if (token_age_h is None or token_age_h > 72) and fresh_7d_pct >= 70:
            result["flags"].append("fresh_wallet_farm")

    if established_pct >= 60:
        result["flags"].append("stable_holders")

    _cache_holder_age(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_holder_age_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        holders=total,
        flags=result["flags"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_holder_age_buckets(token_address: str, chain: str) -> dict[str, Any] | None:
    """Compute holder age buckets + median age from first-seen timestamps."""
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                count() AS total_holders,
                countIf(first_seen >= now() - INTERVAL 24 HOUR) AS fresh_24h,
                countIf(first_seen >= now() - INTERVAL 7 DAY) AS fresh_7d,
                countIf(first_seen >= now() - INTERVAL 30 DAY) AS fresh_30d,
                countIf(first_seen < now() - INTERVAL 30 DAY) AS established,
                medianExact(
                    toFloat64(now()) - toFloat64(first_seen)
                ) AS median_age_seconds
            FROM (
                SELECT to_address AS addr, min(block_timestamp) AS first_seen
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != ''
                GROUP BY addr
            )
            """,
            {"token": token_address, "chain": chain},
        )
        if not rows or not rows[0]:
            return None

        row = rows[0]
        median_seconds = row[5]
        return {
            "total_holders": row[0] or 0,
            "fresh_24h": row[1] or 0,
            "fresh_7d": row[2] or 0,
            "fresh_30d": row[3] or 0,
            "established": row[4] or 0,
            "median_age_hours": (median_seconds / 3600) if median_seconds is not None else None,
        }
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_age_buckets_query_failed: %s", exc)
        return None


def _query_token_age(token_address: str, chain: str) -> dict[str, Any] | None:
    """Get token creation time from deployer_events (oldest event)."""
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT min(block_timestamp) AS created_ts
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s
              AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
        )
        if not rows or not rows[0] or rows[0][0] is None:
            return None

        created_ts = rows[0][0]
        age_hours = None
        if hasattr(created_ts, "timestamp"):
            age_hours = max((time.time() - created_ts.timestamp()) / 3600, 0.0)
        return {
            "created_at": created_ts.isoformat() if hasattr(created_ts, "isoformat") else str(created_ts),
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
        }
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("token_age_query_failed: %s", exc)
        return None


def _query_recent_holders(token_address: str, chain: str) -> list[dict[str, Any]]:
    """Return the most recently-arrived holders with their first-seen age."""
    client = _get_ch_client()
    if client is None:
        return []

    try:
        rows = client.execute(
            """
            SELECT addr, first_seen, transfer_count
            FROM (
                SELECT to_address AS addr,
                       min(block_timestamp) AS first_seen,
                       count() AS transfer_count
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != ''
                GROUP BY addr
            )
            ORDER BY first_seen DESC
            LIMIT 30
            """,
            {"token": token_address, "chain": chain},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("recent_holders_query_failed: %s", exc)
        return []

    holders: list[dict[str, Any]] = []
    for row in rows:
        first_seen = row[1]
        age_hours = None
        if hasattr(first_seen, "timestamp"):
            age_hours = max((time.time() - first_seen.timestamp()) / 3600, 0.0)
        holders.append(
            {
                "address": row[0],
                "first_seen": first_seen.isoformat() if hasattr(first_seen, "isoformat") else str(first_seen),
                "age_hours": round(age_hours, 1) if age_hours is not None else None,
                "transfer_count": row[2],
            }
        )
    return holders


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_holder_age(key: str, data: dict[str, Any]) -> None:
    """Cache holder age data with timestamp."""
    data["_cached_at"] = time.time()
    _HOLDER_AGE_CACHE[key] = data
    if len(_HOLDER_AGE_CACHE) > _HOLDER_AGE_CACHE_MAX:
        _HOLDER_AGE_CACHE.pop(next(iter(_HOLDER_AGE_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _HOLDER_AGE_CACHE,
        _HOLDER_AGE_CACHE_MAX,
        _HOLDER_AGE_CACHE_TTL,
        extra={"clickhouse_connected": _get_ch_client() is not None},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_HOLDER_AGE_CACHE)
