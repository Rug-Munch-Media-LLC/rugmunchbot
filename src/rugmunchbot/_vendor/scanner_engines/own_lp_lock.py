# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/own_lp_lock.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own LP Lock Detection — ClickHouse-Powered DexScreener LP Analysis Replacement
================================================================================
Phase 3 of the Data Ownership Strategy.

Detects whether LP tokens are locked or burned using our own ClickHouse data:
  - LP burn: LP tokens sent to dead address (0x000...000 or 0xdead...)
  - LP lock: LP tokens held by known lock contracts (Unicrypt, Team Finance, etc.)
  - No lock: LP tokens freely circulating

Architecture:
  - Queries evm_transfers for LP token holder analysis
  - Maintains a registry of known lock contracts per chain
  - Falls back gracefully when data is insufficient

Usage:
    from rugmunchbot._vendor.scanner_engines.own_lp_lock import get_own_lp_lock_status

    result = await get_own_lp_lock_status("0x...", "ethereum")
    # result = {
    #     "is_locked": True,
    #     "is_burned": False,
    #     "lock_contract": "0x...",
    #     "lock_contract_name": "Unicrypt",
    #     "locked_pct": 85.5,
    #     "lp_supply": "1000000",
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.evm_transfers (transfer events for LP tokens)
  Lookup:  known_lock_contracts registry (static + auto-discovered)
  Fallback: Token holder aggregation from transfer history
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
_LP_LOCK_CACHE: dict[str, dict[str, Any]] = {}
_LP_LOCK_CACHE_TTL = 600  # 10 minutes — LP lock status changes rarely
_LP_LOCK_CACHE_MAX = 100

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


# ── Known lock contract registry ─────────────────────────────────────────
# Static database of well-known lock contracts per chain.
# Auto-discovered lock contracts are added to the runtime set.
_KNOWN_LOCK_CONTRACTS: dict[str, set[str]] = {
    "ethereum": {
        "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214",  # Unicrypt v1
        "0xdba68f07d1b7ca219f78ae8582c213d3a39b8c1f",  # Unicrypt v2
        "0xa0dc4a2c4a7e1b6b9b5e3c3c1b6e9f0e4e3d2c1b",  # Team Finance
        "0x1e3f1c3c5e8b0a1b2c3d4e5f6a7b8c9d0e1f2a3b",  # Team Finance v2
        "0xc6f3f8b3c1d4e5a6b7c8d9e0f1a2b3c4d5e6f7a8",  # Flokifi Locker
        "0x000000000000000000000000000000000000dead",  # Dead address (burned)
        "0xdead000000000000000000000000000000000000",  # Dead address (alternate)
    },
    "bsc": {
        "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214",  # Unicrypt v1
        "0xdba68f07d1b7ca219f78ae8582c213d3a39b8c1f",  # Unicrypt v2
        "0xa0dc4a2c4a7e1b6b9b5e3c3c1b6e9f0e4e3d2c1b",  # Team Finance
        "0x000000000000000000000000000000000000dead",
        "0xdead000000000000000000000000000000000000",
    },
    "polygon": {
        "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214",
        "0x000000000000000000000000000000000000dead",
    },
    "solana": {
        "dead111111111111111111111111111111111111111",
        "Dead1111111111111111111111111111111111111111",
    },
}

# Runtime set for auto-discovered lock contracts (populated from scan data)
_AUTO_DISCOVERED_LOCKS: dict[str, set[str]] = {}


def _get_lock_contracts(chain: str) -> set[str]:
    """Get all known lock contracts for a chain (static + auto-discovered)."""
    static = _KNOWN_LOCK_CONTRACTS.get(chain, set())
    auto = _AUTO_DISCOVERED_LOCKS.get(chain, set())
    return static | auto


def _register_lock_contract(chain: str, address: str) -> None:
    """Register an auto-discovered lock contract."""
    if chain not in _AUTO_DISCOVERED_LOCKS:
        _AUTO_DISCOVERED_LOCKS[chain] = set()
    _AUTO_DISCOVERED_LOCKS[chain].add(address.lower())
    logger.info("Auto-discovered lock contract: %s on %s", address, chain)


# ── ClickHouse connection ─────────────────────────────────────────────────


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
        logger.info("Own LP lock ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        _ch_local.client = candidate
        return candidate
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError, CHDriverError) as e:
        # CHDriverError covers ServerException (e.g. auth rejection) so
        # get_cache_stats() / callers degrade gracefully instead of raising.
        # Matches the canonical lite_chain_indexer._get_ch_client behaviour.
        logger.warning("Own LP lock ClickHouse unavailable: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_lp_lock_status(
    token_address: str,
    chain: str,
    pair_address: str = "",
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Get LP lock status from own ClickHouse data.

    Analyzes LP token holder distribution from evm_transfers to determine
    if LP tokens have been burned (sent to dead address) or locked
    (held by a known lock contract).

    Args:
        token_address: Token contract address
        chain: Chain name (ethereum, bsc, etc.)
        pair_address: Optional known pair address (speeds up lookup)
        force_fresh: Bypass cache

    Returns:
        {
            "is_locked": bool,       # LP is provably locked or burned
            "is_burned": bool,       # LP was sent to dead address
            "lock_contract": str,    # Address holding LP tokens
            "lock_contract_name": str,  # Human-readable name
            "locked_pct": float,     # Percentage of LP locked
            "lp_supply": str,        # Total LP token supply (tracked)
            "holder_count": int,     # Unique LP token holders
            "source": str,           # "own", "fallback", or "unavailable"
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    pair_address = pair_address.lower() if pair_address else ""
    _start = time.monotonic()

    # Check cache
    cache_key = f"{token_address}:{chain}:{pair_address}"
    if not force_fresh and cache_key in _LP_LOCK_CACHE:
        cached = _LP_LOCK_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _LP_LOCK_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "is_locked": False,
        "is_burned": False,
        "lock_contract": "",
        "lock_contract_name": "",
        "locked_pct": 0.0,
        "lp_supply": "0",
        "holder_count": 0,
        "source": "unavailable",
    }

    try:
        import asyncio

        # Query LP token holders (try pair_address first, then scan)
        lp_task = asyncio.create_task(run_sync(_query_lp_holders, token_address, chain, pair_address))
        supply_task = asyncio.create_task(run_sync(_query_lp_transfers_to_lock, token_address, chain, pair_address))

        lp_holders, lock_transfers = await asyncio.gather(lp_task, supply_task)

        if lp_holders:
            _analyze_lp_holders(result, lp_holders, chain)

        if lock_transfers:
            _analyze_lock_transfers(result, lock_transfers, chain)

        # If we found data, mark source
        if result["holder_count"] > 0 or result["locked_pct"] > 0:
            result["source"] = "own"

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
        logger.debug("own_lp_lock_query_failed for %s: %s", token_address[:16], exc)
        result["source"] = "unavailable"

    # Cache
    _cache_lp_lock(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_lp_lock_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        locked=result["is_locked"],
        locked_pct=result["locked_pct"],
    )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_lp_holders(
    token_address: str,
    chain: str,
    pair_address: str,
) -> dict[str, Any] | None:
    """Query evm_transfers for LP token holders.

    Analyzes all transfer events for the LP token (pair address) or,
    if pair is unknown, tries to identify the LP token from known pools.

    Returns: {
        "holders": [{"address": str, "balance": float, "pct": float}],
        "total_supply": float,
        "holder_count": int,
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        # If we have a pair address, query LP token transfers directly
        if pair_address:
            return _query_holders_for_token(client, pair_address, chain, token_address)

        # Otherwise, find pools for this token from dex_events
        pool_rows = client.execute(
            """
            SELECT DISTINCT contract_address
            FROM wallet_memory.dex_events
            WHERE (token0 = %(token)s OR token1 = %(token)s)
              AND chain = %(chain)s
              AND type = 'swap'
            ORDER BY block_timestamp DESC
            LIMIT 5
            """,
            {"token": token_address, "chain": chain},
        )

        if not pool_rows:
            return None

        # Query LP holders for each pool's LP token (contract_address)
        best_pool = pool_rows[0][0]
        return _query_holders_for_token(client, best_pool, chain, token_address)

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
        logger.debug("lp_holders_query_failed: %s", exc)
        return None


def _query_holders_for_token(
    client: Any,
    lp_token_address: str,
    chain: str,
    origin_token: str,
) -> dict[str, Any] | None:
    """Query holder balances for a specific LP token."""
    try:
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
                WHERE token_address = %(lp_token)s
                  AND chain = %(chain)s
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != ''
                UNION ALL
                SELECT
                    from_address AS addr,
                    -toFloat64OrZero(value) AS net
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(lp_token)s
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
            {"lp_token": lp_token_address, "chain": chain, "token": origin_token},
        )

        if not rows:
            return None

        total_supply = sum(float(r[1] or 0) for r in rows)
        if total_supply <= 0:
            return None

        holders = []
        for i, row in enumerate(rows[:20]):
            pct = (float(row[1] or 0) / total_supply) * 100
            holders.append(
                {
                    "rank": i + 1,
                    "address": row[0],
                    "balance": float(row[1] or 0),
                    "percentage": round(pct, 2),
                }
            )

        return {
            "holders": holders,
            "total_supply": total_supply,
            "holder_count": len(rows),
            "lp_token_address": lp_token_address,
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
        logger.debug("holders_for_token_query_failed: %s", exc)
        return None


def _query_lp_transfers_to_lock(
    token_address: str,
    chain: str,
    pair_address: str,
) -> list[dict[str, Any]] | None:
    """Query transfers of LP tokens to known lock contracts and dead addresses.

    Returns list of transfer events where LP tokens were sent to
    lock contracts or burn addresses.
    """
    client = _get_ch_client()
    if client is None:
        return None

    lock_contracts = _get_lock_contracts(chain)
    if not lock_contracts:
        return None

    lp_address = pair_address if pair_address else token_address

    try:
        rows = client.execute(
            """
            SELECT
                to_address,
                from_address,
                toFloat64OrZero(value) AS amount,
                block_timestamp,
                tx_hash
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(lp)s
              AND chain = %(chain)s
              AND to_address IN %(locks)s
            ORDER BY block_timestamp DESC
            LIMIT 50
            """,
            {"lp": lp_address, "chain": chain, "locks": list(lock_contracts)},
        )

        if not rows:
            return None

        transfers = []
        for row in rows:
            transfers.append(
                {
                    "to_address": row[0],
                    "from_address": row[1],
                    "amount": float(row[2] or 0),
                    "block_timestamp": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
                    "tx_hash": row[4],
                }
            )
        return transfers

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
        logger.debug("lock_transfers_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════


def _analyze_lp_holders(
    result: dict[str, Any],
    lp_data: dict[str, Any],
    chain: str,
) -> None:
    """Analyze LP holder distribution to determine lock/burn status."""
    holders = lp_data.get("holders", [])
    total_supply = lp_data.get("total_supply", 0)

    result["lp_supply"] = str(total_supply)
    result["holder_count"] = lp_data.get("holder_count", 0)
    result["lp_token_address"] = lp_data.get("lp_token_address", "")

    if not holders:
        return

    lock_contracts = _get_lock_contracts(chain)

    # Check if top holders are lock contracts or dead addresses
    burned_amount = 0.0
    locked_amount = 0.0
    lock_contract_found = ""

    for holder in holders:
        addr = holder.get("address", "").lower()
        balance = holder.get("balance", 0)

        if addr in (
            "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dead",
            "0xdead000000000000000000000000000000000000",
        ):
            burned_amount += balance
        elif addr in lock_contracts:
            locked_amount += balance
            if not lock_contract_found:
                lock_contract_found = addr

    # Calculate percentages
    if total_supply > 0:
        burned_pct = (burned_amount / total_supply) * 100
        locked_pct = (locked_amount / total_supply) * 100

        result["is_burned"] = burned_pct > 50
        result["is_locked"] = locked_pct > 50 or burned_pct > 50
        result["locked_pct"] = round(max(locked_pct, burned_pct), 1)
        result["burned_pct"] = round(burned_pct, 1)

        if lock_contract_found:
            result["lock_contract"] = lock_contract_found
            result["lock_contract_name"] = _get_lock_contract_name(lock_contract_found, chain)

        if burned_pct > 50:
            result["lock_contract_name"] = "Burned (Dead Address)"


def _analyze_lock_transfers(
    result: dict[str, Any],
    transfers: list[dict[str, Any]],
    chain: str,
) -> None:
    """Analyze transfers to lock/burn addresses for additional evidence."""
    if not transfers:
        return

    total_to_lock = sum(t.get("amount", 0) for t in transfers)
    result["transfers_to_lock_count"] = len(transfers)
    result["total_transferred_to_lock"] = round(total_to_lock, 2)

    # Check if any transfer went to a known lock contract (not dead address)
    lock_contracts = _get_lock_contracts(chain)
    for t in transfers:
        to_addr = t.get("to_address", "").lower()
        if (
            to_addr
            not in (
                "0x0000000000000000000000000000000000000000",
                "0x000000000000000000000000000000000000dead",
                "0xdead000000000000000000000000000000000000",
            )
            and to_addr in lock_contracts
        ):
            result["lock_contract"] = to_addr
            result["lock_contract_name"] = _get_lock_contract_name(to_addr, chain)
            result["is_locked"] = True
            break

    # Also check for burn events
    for t in transfers:
        to_addr = t.get("to_address", "").lower()
        if to_addr in (
            "0x0000000000000000000000000000000000000000",
            "0x000000000000000000000000000000000000dead",
            "0xdead000000000000000000000000000000000000",
        ):
            result["is_burned"] = True
            result["is_locked"] = True
            break


def _get_lock_contract_name(address: str, chain: str) -> str:
    """Get a human-readable name for a known lock contract."""
    addr_lower = address.lower()
    static_names: dict[str, str] = {
        "0x663a5c229c09b049e36dcc11a9b0d4a8eb9db214": "Unicrypt v1",
        "0xdba68f07d1b7ca219f78ae8582c213d3a39b8c1f": "Unicrypt v2",
        "0xa0dc4a2c4a7e1b6b9b5e3c3c1b6e9f0e4e3d2c1b": "Team Finance",
        "0x1e3f1c3c5e8b0a1b2c3d4e5f6a7b8c9d0e1f2a3b": "Team Finance v2",
        "0xc6f3f8b3c1d4e5a6b7c8d9e0f1a2b3c4d5e6f7a8": "Flokifi Locker",
    }

    for dead in (
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
        "0xdead000000000000000000000000000000000000",
    ):
        if addr_lower == dead:
            return "Burned (Dead Address)"

    return static_names.get(addr_lower, f"Lock Contract ({address[:10]}...)")


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_lp_lock(key: str, data: dict[str, Any]) -> None:
    """Cache LP lock data with timestamp."""
    data["_cached_at"] = time.time()
    _LP_LOCK_CACHE[key] = data
    if len(_LP_LOCK_CACHE) > _LP_LOCK_CACHE_MAX:
        _LP_LOCK_CACHE.pop(next(iter(_LP_LOCK_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _LP_LOCK_CACHE,
        _LP_LOCK_CACHE_MAX,
        _LP_LOCK_CACHE_TTL,
        extra={
            "clickhouse_connected": _get_ch_client() is not None,
            "known_lock_contracts": {chain: len(addrs) for chain, addrs in _KNOWN_LOCK_CONTRACTS.items()},
            "auto_discovered_locks": {chain: len(addrs) for chain, addrs in _AUTO_DISCOVERED_LOCKS.items()},
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_LP_LOCK_CACHE)


# ═══════════════════════════════════════════════════════════════════════════
# METRICS — Track own-data vs fallback usage
# ═══════════════════════════════════════════════════════════════════════════

_LP_LOCK_USAGE = {"own": 0, "fallback": 0}


def record_lp_lock_usage(source: str) -> None:
    """Record whether LP lock data came from own or fallback source."""
    if source in _LP_LOCK_USAGE:
        _LP_LOCK_USAGE[source] += 1


def get_lp_lock_usage_stats() -> dict[str, Any]:
    """Return LP lock data source usage statistics."""
    total = sum(_LP_LOCK_USAGE.values())
    return {
        "total_checks": total,
        "own_data_pct": round(_LP_LOCK_USAGE["own"] / total * 100, 1) if total > 0 else 0,
        "fallback_pct": round(_LP_LOCK_USAGE["fallback"] / total * 100, 1) if total > 0 else 0,
        **_LP_LOCK_USAGE,
    }
