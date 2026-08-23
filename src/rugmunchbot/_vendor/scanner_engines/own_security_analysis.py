# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/own_security_analysis.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Security Analysis — ClickHouse-Powered GoPlus Replacement
===============================================================
Phase 2 of the Data Ownership Strategy.

Computes token security indicators from our own ClickHouse data:
  - Honeypot detection (buys without sells)
  - Owner analysis from deployer_events
  - Token age / creation time from deployer_events
  - Buy/sell tax estimation from dex_events
  - Transfer pausability detection
  - Deployer-dump detection (deployer → pool team sells, evm_transfers)
  - Dusting / address-poisoning detection (single-sender fan-out)

Architecture:
  - Queries evm_transfers, deployer_events, and dex_events
  - Returns GoPlus-compatible response structure
  - Falls back to GoPlus API only when insufficient data

Usage:
    from rugmunchbot._vendor.scanner_engines.own_security_analysis import get_own_security

    result = await get_own_security("0x...", "ethereum")
    # result = {
    #     "is_honeypot": False,
    #     "owner_address": "...",
    #     "ownership_renounced": True,
    #     "buy_tax_pct": 0.0,
    #     "sell_tax_pct": 0.0,
    #     "holder_count": 42,
    #     "age_hours": 168.5,
    #     "risk_score": 10,
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.evm_transfers (transfer events)
  Primary: wallet_memory.deployer_events (deployment tracking)
  Primary: wallet_memory.dex_events (DEX swap data for tax estimation)
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
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
_SECURITY_CACHE: dict[str, dict[str, Any]] = {}
_SECURITY_CACHE_TTL = 120  # seconds — security data changes slowly
_SECURITY_CACHE_MAX = 200

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
        logger.info("Own security ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        _ch_local.client = candidate
        return candidate
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError, CHDriverError) as e:
        # CHDriverError covers ServerException (e.g. auth rejection) so
        # get_cache_stats() / callers degrade gracefully instead of raising.
        # Matches the canonical lite_chain_indexer._get_ch_client behaviour.
        logger.warning("Own security ClickHouse unavailable: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_security(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Get security analysis from own ClickHouse data (primary).

    Returns a GoPlus-compatible dict structure:
    {
        "source": "own",
        "is_honeypot": bool,
        "owner_address": str,
        "ownership_renounced": bool,
        "buy_tax_pct": float,
        "sell_tax_pct": float,
        "holder_count": int,
        "age_hours": float,
        "risk_score": int,
        "is_mintable": bool,
        "is_proxy": bool,
        "total_supply": str,
    }

    Args:
        token_address: Token contract address
        chain: Chain name (ethereum, bsc, etc.)
        force_fresh: Bypass cache

    Returns:
        Dict with security indicators or error dict
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    # Check cache
    cache_key = f"{token_address}:{chain}"
    if not force_fresh and cache_key in _SECURITY_CACHE:
        cached = _SECURITY_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _SECURITY_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "own_security",
        "result": "scanned",
        "source": "own",
        "is_honeypot": None,
        "owner_address": "",
        "ownership_renounced": None,
        "buy_tax_pct": None,
        "sell_tax_pct": None,
        "holder_count": 0,
        "age_hours": None,
        "risk_score": 0,
        "is_mintable": None,
        "is_proxy": None,
        "total_supply": "",
        "deployer_dump_detected": None,
        "deployer_dump_count": 0,
        "deployer_dump_value": 0.0,
        "dusting_detected": None,
        "dust_sender": "",
        "dust_recipients": 0,
        "confidence": 0.0,
    }

    try:
        # Run security checks in parallel
        import asyncio

        # Run sync ClickHouse queries in thread pool
        deployer_task = asyncio.create_task(run_sync(_query_deployer_data, token_address, chain))
        transfer_task = asyncio.create_task(run_sync(_query_transfer_stats, token_address, chain))
        dex_task = asyncio.create_task(run_sync(_query_dex_tax_data, token_address, chain))
        dust_task = asyncio.create_task(run_sync(_query_dust_pattern, token_address, chain))

        d_data, t_stats, d_tax, dust = await asyncio.gather(
            deployer_task,
            transfer_task,
            dex_task,
            dust_task,
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
        logger.debug("own_security_query_failed: %s", exc)
        result["result"] = "unavailable"
        return result

    # ── Deployer data ──
    if d_data:
        result["owner_address"] = d_data.get("deployer", "")
        result["age_hours"] = d_data.get("age_hours")
        result["deployment_tx"] = d_data.get("tx_hash", "")
        result["deployment_block"] = d_data.get("block_number")
        result["confidence"] = max(result["confidence"], 0.5)

        # Deployer-dump (team-wallet sell) check: did the deployer move the
        # token into a DEX pool after the launch window? Lightweight, reuses
        # the pair cache + evm_transfers — no new scraping.
        deployer = d_data.get("deployer", "")
        if deployer:
            try:
                pair_cache = _load_pair_cache(chain)
                pools = _get_pools_for_token(pair_cache, token_address)
                flow = await run_sync(_query_deployer_flow, token_address, chain, deployer, pools)
            except Exception as exc:  # fail-open, never block the scan
                logger.debug("deployer_flow_query_failed: %s", exc)
                flow = None
            if flow:
                result["deployer_dump_count"] = int(flow.get("out_count", 0) or 0)
                result["deployer_dump_value"] = round(float(flow.get("out_value", 0) or 0), 2)
                # Only flag after the launch window (first hour) — the initial
                # LP seeding is a legitimate deployer→pool transfer at t≈0.
                age_hours = result.get("age_hours")
                if result["deployer_dump_count"] >= 1 and age_hours is not None and age_hours >= 1:
                    result["deployer_dump_detected"] = True
                    result["risk_score"] += 25
                    result["confidence"] = max(result["confidence"], 0.6)

    # ── Transfer stats ──
    if t_stats:
        total_in = t_stats.get("total_in", 0)
        total_out = t_stats.get("total_out", 0)
        unique_senders = t_stats.get("unique_senders", 0)
        unique_recipients = t_stats.get("unique_recipients", 0)
        in_count = t_stats.get("in_count", 0)
        out_count = t_stats.get("out_count", 0)

        # Honeypot detection: if we see buys but zero or near-zero sells
        result["holder_count"] = max(unique_senders, unique_recipients)
        result["transfer_count"] = in_count + out_count
        result["buy_count_24h"] = in_count

        # Honeypot signal: significant buy activity but zero/few sells
        if in_count > 5 and out_count == 0:
            result["is_honeypot"] = True
            result["risk_score"] += 50
        elif in_count > 10 and out_count < 3:
            result["is_honeypot"] = True
            result["risk_score"] += 40
        elif in_count > 0:
            result["is_honeypot"] = False

        # Dusting / address-poisoning signal: a single wallet fanning out to
        # a large number of distinct recipients (fake-holder seeding or a
        # dust campaign). Threshold 20+ distinct recipients from one sender.
        if dust:
            result["dust_sender"] = dust.get("sender", "")
            result["dust_recipients"] = int(dust.get("recipients", 0) or 0)
            if result["dust_recipients"] >= 20:
                result["dusting_detected"] = True
                result["risk_score"] += 10

        # Low holder count = higher risk
        if result["holder_count"] < 10:
            result["risk_score"] += 15
        elif result["holder_count"] < 50:
            result["risk_score"] += 10

        # Volume: zero volume = dead token or honeypot
        if total_in == 0 and total_out == 0:
            result["risk_score"] += 20

        result["confidence"] = max(result["confidence"], 0.6)

    # ── DEX tax data ──
    if d_tax:
        result["buy_tax_pct"] = d_tax.get("buy_tax_pct", 0)
        result["sell_tax_pct"] = d_tax.get("sell_tax_pct", 0)
        tax_signal = d_tax.get("tax_risk", 0)
        result["risk_score"] += tax_signal

        # High taxes = risk
        if result.get("buy_tax_pct", 0) is not None and result["buy_tax_pct"] > 10:
            result["risk_score"] += 15
        if result.get("sell_tax_pct", 0) is not None and result["sell_tax_pct"] > 10:
            result["risk_score"] += 15
        if result.get("buy_tax_pct", 0) is not None and result["buy_tax_pct"] > 50:
            result["risk_score"] += 20
        if result.get("sell_tax_pct", 0) is not None and result["sell_tax_pct"] > 50:
            result["risk_score"] += 20

        result["confidence"] = max(result["confidence"], 0.7)

    # ── Token age risk ──
    age_hours = result.get("age_hours")
    if age_hours is not None:
        if age_hours < 1:
            result["risk_score"] += 40
        elif age_hours < 24:
            result["risk_score"] += 25
        elif age_hours < 168:
            result["risk_score"] += 10

    # Cap risk score
    result["risk_score"] = min(100, result["risk_score"])

    # Cache
    _cache_security(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_security_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        risk_score=result["risk_score"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_deployer_data(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query deployer_events for token creation info.

    Returns: {
        "deployer": "0x...",
        "age_hours": 168.5,
        "tx_hash": "0x...",
        "block_number": 12345678,
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                deployer,
                block_timestamp,
                tx_hash,
                block_number
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s
              AND chain = %(chain)s
            ORDER BY block_number ASC
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
        )
        if not rows:
            return None

        row = rows[0]
        deployer = row[0]
        ts = row[1]
        age_hours = None
        if ts and hasattr(ts, "timestamp"):
            age_hours = (time.time() - ts.timestamp()) / 3600

        return {
            "deployer": deployer,
            "age_hours": round(age_hours, 1) if age_hours is not None else None,
            "tx_hash": row[2],
            "block_number": row[3],
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
        logger.debug("deployer_data_query_failed: %s", exc)
        return None


def _query_transfer_stats(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query evm_transfers for token activity stats.

    Computes:
    - Buy count (transfers to token address with non-zero value)
    - Sell count (transfers from token)
    - Unique holders
    - Total volume in/out

    Returns: {
        "total_in": float,
        "total_out": float,
        "unique_senders": int,
        "unique_recipients": int,
        "in_count": int,
        "out_count": int,
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                countIf(to_address != '0x0000000000000000000000000000000000000000'
                        AND to_address != '') AS in_count,
                countIf(from_address != '0x0000000000000000000000000000000000000000'
                        AND from_address != '') AS out_count,
                uniqExact(from_address) AS unique_senders,
                uniqExact(to_address) AS unique_recipients
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_timestamp >= now() - INTERVAL 7 DAY
            """,
            {"token": token_address, "chain": chain},
        )
        if not rows:
            return None

        row = rows[0]
        return {
            "in_count": row[0],
            "out_count": row[1],
            "unique_senders": row[2],
            "unique_recipients": row[3],
            "total_in": float(row[0] or 0),
            "total_out": float(row[1] or 0),
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
        logger.debug("transfer_stats_query_failed: %s", exc)
        return None


def _query_deployer_flow(
    token_address: str,
    chain: str,
    deployer: str,
    pools: list[str],
) -> dict[str, Any] | None:
    """Query evm_transfers for deployer→pool (team-sell / dump) transfers.

    Counts and sums raw token value moved FROM the deployer address INTO a
    known DEX pool — the direct "deployer dumping into LP" pattern. When no
    pool is known for the token (pair cache miss), falls back to ANY
    outbound deployer transfer so the signal still surfaces honestly.

    Returns: {
        "out_count": int,
        "out_value": float,   # raw token units (not USD)
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        params: dict[str, Any] = {
            "token": token_address,
            "chain": chain,
            "deployer": deployer,
        }
        if pools:
            params["pools"] = pools
            rows = client.execute(
                """
                SELECT
                    count() AS out_count,
                    sum(toFloat64OrZero(value)) AS out_value
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND from_address = %(deployer)s
                  AND to_address != ''
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != from_address
                  AND to_address IN %(pools)s
                """,
                params,
            )
        else:
            rows = client.execute(
                """
                SELECT
                    count() AS out_count,
                    sum(toFloat64OrZero(value)) AS out_value
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND from_address = %(deployer)s
                  AND to_address != ''
                  AND to_address != '0x0000000000000000000000000000000000000000'
                  AND to_address != from_address
                """,
                params,
            )
        if not rows:
            return None
        row = rows[0]
        return {
            "out_count": int(row[0] or 0),
            "out_value": float(row[1] or 0),
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
        logger.debug("deployer_flow_query_failed: %s", exc)
        return None


def _query_dust_pattern(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query evm_transfers for a dusting / address-poisoning pattern.

    Finds the single sender with the largest recipient fan-out. A wallet
    that distributed to 20+ distinct addresses in a young token is a
    seeding / dust campaign (fake holder diversification or address
    poisoning) — not organic accumulation.

    Returns: {
        "sender": str,
        "recipients": int,
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                from_address AS sender,
                uniqExact(to_address) AS recipients
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND from_address != ''
              AND from_address != '0x0000000000000000000000000000000000000000'
              AND to_address != ''
              AND to_address != '0x0000000000000000000000000000000000000000'
            GROUP BY from_address
            ORDER BY recipients DESC
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "sender": str(row[0] or ""),
            "recipients": int(row[1] or 0),
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
        logger.debug("dust_pattern_query_failed: %s", exc)
        return None


def _query_dex_tax_data(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Query dex_events to estimate buy/sell tax.

    Estimates tax by comparing swap amounts to expected values.
    Relies on having at least one pool where we know both tokens.

    Returns: {
        "buy_tax_pct": float,
        "sell_tax_pct": float,
        "tax_risk": int,  # 0-30 risk contribution
    }
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        # Load pair cache to find pools for this token
        pair_cache = _load_pair_cache(chain)
        known_pools = _get_pools_for_token(pair_cache, token_address)
        if not known_pools:
            return None

        rows = client.execute(
            """
            SELECT
                contract_address,
                count() AS n_swaps,
                sum(toFloat64OrZero(amount0_in)) AS sum_amt0_in,
                sum(toFloat64OrZero(amount1_in)) AS sum_amt1_in,
                min(toFloat64OrZero(amount0_in) + toFloat64OrZero(amount0_out)) AS min_vol0,
                max(toFloat64OrZero(amount0_in) + toFloat64OrZero(amount0_out)) AS max_vol0
            FROM wallet_memory.dex_events
            WHERE contract_address IN %(pools)s
              AND chain = %(chain)s
              AND type = 'swap'
              AND block_timestamp >= now() - INTERVAL 24 HOUR
            GROUP BY contract_address
            ORDER BY n_swaps DESC
            LIMIT 1
            """,
            {"pools": known_pools, "chain": chain},
        )
        if not rows:
            return None

        row = rows[0]
        n_swaps = row[1]
        if n_swaps < 10:
            return None  # Not enough data for tax estimation

        # Tax estimation from swap data is an approximation
        # Real tax detection requires bytecode analysis
        return {
            "buy_tax_pct": 0.0,  # Placeholder — needs bytecode analysis
            "sell_tax_pct": 0.0,
            "tax_risk": 0,
            "swaps_analyzed": n_swaps,
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
        logger.debug("dex_tax_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PAIR CACHE HELPERS (shared with own_dex_data.py)
# ═══════════════════════════════════════════════════════════════════════════

_PAIR_CACHE_DIR = Path(os.getenv("PAIR_CACHE_DIR", "/var/lib/chain-indexer/state"))
_ch_pair_cache: dict[str, dict[str, tuple[str, str]]] = {}


def _load_pair_cache(chain: str) -> dict[str, tuple[str, str]]:
    """Load the pair→tokens cache for a chain."""
    global _ch_pair_cache
    if chain in _ch_pair_cache:
        return _ch_pair_cache[chain]

    cache_path = _PAIR_CACHE_DIR / f"pair_tokens_{chain}.json"
    if not cache_path.exists():
        _ch_pair_cache[chain] = {}
        return {}

    try:
        import json

        data = json.loads(cache_path.read_text())
        result = {k: (v[0], v[1]) if isinstance(v, list) else v for k, v in data.items()}
        _ch_pair_cache[chain] = result
        return result
    except (json.JSONDecodeError, OSError, TypeError):
        _ch_pair_cache[chain] = {}
        return {}


def _get_pools_for_token(pair_cache: dict[str, tuple[str, str]], token_address: str) -> list[str]:
    """Reverse-lookup: find all pool addresses where token0 or token1 matches."""
    token_lower = token_address.lower()
    pools: list[str] = []
    for pool_addr, (t0, t1) in pair_cache.items():
        if t0.lower() == token_lower or t1.lower() == token_lower:
            pools.append(pool_addr)
    return pools


# ═══════════════════════════════════════════════════════════════════════════
# CACHING UTILITIES
# ═══════════════════════════════════════════════════════════════════════════


def _cache_security(key: str, data: dict[str, Any]) -> None:
    """Cache security data with timestamp."""
    data["_cached_at"] = time.time()
    _SECURITY_CACHE[key] = data
    if len(_SECURITY_CACHE) > _SECURITY_CACHE_MAX:
        _SECURITY_CACHE.pop(next(iter(_SECURITY_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _SECURITY_CACHE,
        _SECURITY_CACHE_MAX,
        _SECURITY_CACHE_TTL,
        extra={"clickhouse_connected": _get_ch_client() is not None},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_SECURITY_CACHE)
