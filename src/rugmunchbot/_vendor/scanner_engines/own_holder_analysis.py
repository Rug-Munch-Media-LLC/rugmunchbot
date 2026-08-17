# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_holder_analysis.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Holder Analysis — ClickHouse-Powered Blockscout Holder Replacement
======================================================================
Phase 2 of the Data Ownership Strategy.

Computes holder metrics from our own ClickHouse transfer data:
  - Top holders by balance from evm_transfers
  - Holder concentration (top 1/5/10 percentages)
  - Total holder count
  - Gini coefficient approximation
  - New holder count (holders first seen in last 24h)

Architecture:
  - Queries evm_transfers to compute holder balances
  - Returns Blockscout/DexScreener-compatible response structure
  - Falls back to RPC/Blockscout only when insufficient data

Usage:
    from rugmunchbot._vendor.scanner_engines.own_holder_analysis import get_own_holder_analysis

    result = await get_own_holder_analysis("0x...", "ethereum")
    # result = {
    #     "holder_count": 42,
    #     "top_holders": [{"rank": 1, "address": "0x...", "percentage": 12.5}, ...],
    #     "concentration_pct": 12.5,
    #     "gini_coefficient": 0.85,
    #     "new_holders_24h": 5,
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.evm_transfers (all transfer events)
"""

from __future__ import annotations

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
_HOLDER_CACHE: dict[str, dict[str, Any]] = {}
_HOLDER_CACHE_TTL = 300  # 5 minutes — holder data changes relatively slowly
_HOLDER_CACHE_MAX = 100

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
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client."""
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
        logger.info("Own holder analysis ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        _ch_local.client = candidate
        return candidate
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own holder analysis ClickHouse unavailable: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_holder_analysis(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Get holder analysis from own ClickHouse data.

    Computes holder distribution from evm_transfers by tracking
    net balance changes per address.

    Returns:
        {
            "holder_count": int,
            "top_holders": [{"rank": 1, "address": "0x...", "percentage": 12.5}, ...],
            "concentration_pct": float,  # top1 percentage
            "top5_pct": float,
            "top10_pct": float,
            "gini_coefficient": float,
            "new_holders_24h": int,
            "total_supply_tracked": str,  # sum of all tracked balances
            "source": "own",
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    # Check cache
    cache_key = f"{token_address}:{chain}"
    if not force_fresh and cache_key in _HOLDER_CACHE:
        cached = _HOLDER_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _HOLDER_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "holders",
        "result": "scanned",
        "source": "own",
        "holder_count": 0,
        "top_holders": [],
        "concentration_pct": 0.0,
        "top5_pct": 0.0,
        "top10_pct": 0.0,
        "gini_coefficient": 0.0,
        "new_holders_24h": 0,
        "total_supply_tracked": "0",
        "confidence": 0.0,
    }

    try:
        # Run queries in parallel
        import asyncio

        top_task = asyncio.create_task(run_sync(_query_top_holders, token_address, chain))
        new_task = asyncio.create_task(run_sync(_query_new_holders, token_address, chain))
        gini_task = asyncio.create_task(run_sync(_query_gini_data, token_address, chain))

        top_data, new_data, gini_data = await asyncio.gather(
            top_task,
            new_task,
            gini_task,
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
        logger.debug("holder_query_failed for %s: %s", token_address[:16], exc)
        result["result"] = "unavailable"
        result["source"] = "unavailable"
        return result

    # ── Top holders ──
    if top_data:
        holders = top_data.get("holders", [])
        total_balance = top_data.get("total_balance", 0)

        result["holder_count"] = top_data.get("holder_count", 0)
        result["top_holders"] = holders
        result["total_supply_tracked"] = str(total_balance)

        if holders:
            result["concentration_pct"] = holders[0].get("percentage", 0)

        # Calculate top5 and top10 percentages
        top5_pct = sum(h.get("percentage", 0) for h in holders[:5])
        top10_pct = sum(h.get("percentage", 0) for h in holders[:10])
        result["top5_pct"] = round(top5_pct, 2)
        result["top10_pct"] = round(top10_pct, 2)
        result["confidence"] = max(result["confidence"], 0.7)

    # ── Gini coefficient ──
    if gini_data:
        result["gini_coefficient"] = round(gini_data.get("gini", 0.0), 4)
        result["confidence"] = max(result["confidence"], 0.6)

    # ── New holders ──
    if new_data:
        result["new_holders_24h"] = new_data.get("new_holders", 0)

    # Cache
    _cache_holders(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_holder_analysis_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        holders=result["holder_count"],
    )

    return result


async def get_holder_count(
    token_address: str,
    chain: str,
) -> int:
    """Quick helper: get just the holder count for a token."""
    result = await get_own_holder_analysis(token_address, chain)
    return result.get("holder_count", 0)


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_top_holders(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query evm_transfers to compute current holder balances.

    Estimates balance by net transfers: total received - total sent.
    This is an approximation (doesn't account for initial mint or burns).

    Returns: {
        "holders": [{"rank": 1, "address": "0x...", "percentage": 12.5}, ...],
        "holder_count": int,
        "total_balance": float,
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        # Compute net balance per address from transfer history
        rows = client.execute(
            """
            SELECT
                addr,
                sum(net) AS balance
            FROM (
                SELECT
                    to_address AS addr,
                    toFloat64OrZero(value) AS net
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != ''
                UNION ALL
                SELECT
                    from_address AS addr,
                    -toFloat64OrZero(value) AS net
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND from_address != '0x0000000000000000000000000000000000000000'
                  AND from_address != ''
            )
            WHERE addr != ''
            GROUP BY addr
            HAVING balance > 0
            ORDER BY balance DESC
            LIMIT 50
            """,
            {"token": token_address, "chain": chain},
        )

        if not rows:
            return None

        total_balance = sum(float(r[1] or 0) for r in rows)
        if total_balance <= 0:
            return None

        holders = []
        for i, row in enumerate(rows[:20]):
            pct = (float(row[1] or 0) / total_balance) * 100
            holders.append(
                {
                    "rank": i + 1,
                    "address": row[0],
                    "amount": str(row[1]),
                    "percentage": round(pct, 2),
                }
            )

        return {
            "holders": holders,
            "holder_count": len(rows),
            "total_balance": total_balance,
        }
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
        logger.debug("top_holders_query_failed: %s", exc)
        return None


def _query_new_holders(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Count addresses that first appeared in the last 24h.

    Returns: {"new_holders": int}
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT count()
            FROM (
                SELECT to_address AS addr
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != ''
                  AND block_timestamp >= now() - INTERVAL 24 HOUR
                GROUP BY addr
            ) AS recent
            WHERE addr NOT IN (
                SELECT DISTINCT to_address
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != ''
                  AND block_timestamp < now() - INTERVAL 24 HOUR
            )
            """,
            {"token": token_address, "chain": chain},
        )

        if rows:
            return {"new_holders": rows[0][0]}
        return {"new_holders": 0}
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
        logger.debug("new_holders_query_failed: %s", exc)
        return None


def _query_gini_data(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Compute Gini coefficient from holder balances.

    The Gini coefficient measures inequality: 0 = perfect equality,
    1 = perfect inequality (one holder has everything).

    Returns: {"gini": float}
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        # Get all non-zero holder balances
        rows = client.execute(
            """
            SELECT balance FROM (
                SELECT
                    addr,
                    sum(net) AS balance
                FROM (
                    SELECT to_address AS addr,
                           toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND to_address != '0x0000000000000000000000000000000000000000'
                      AND to_address != ''
                    UNION ALL
                    SELECT from_address AS addr,
                           -toFloat64OrZero(value) AS net
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND from_address != '0x0000000000000000000000000000000000000000'
                      AND from_address != ''
                )
                WHERE addr != ''
                GROUP BY addr
                HAVING balance > 0
            )
            ORDER BY balance ASC
            """,
            {"token": token_address, "chain": chain},
        )

        if not rows or len(rows) < 2:
            return None

        balances = [float(r[0] or 0) for r in rows]
        n = len(balances)
        total = sum(balances)
        if total <= 0:
            return None

        # Gini = (2 * sum(i * yi) / (n * sum(yi))) - (n + 1) / n
        # where yi are sorted ascending balances
        cum_sum = 0.0
        weighted_sum = 0.0
        for i, b in enumerate(balances):
            i_zero = i + 1  # 1-indexed
            cum_sum += b
            weighted_sum += i_zero * b

        gini = (2 * weighted_sum) / (n * total) - (n + 1) / n
        gini = max(0.0, min(1.0, gini))

        return {"gini": round(gini, 4)}
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
        logger.debug("gini_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_holders(key: str, data: dict[str, Any]) -> None:
    """Cache holder data with timestamp."""
    data["_cached_at"] = time.time()
    _HOLDER_CACHE[key] = data
    if len(_HOLDER_CACHE) > _HOLDER_CACHE_MAX:
        _HOLDER_CACHE.pop(next(iter(_HOLDER_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _HOLDER_CACHE,
        _HOLDER_CACHE_MAX,
        _HOLDER_CACHE_TTL,
        extra={"clickhouse_connected": _get_ch_client() is not None},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_HOLDER_CACHE)
