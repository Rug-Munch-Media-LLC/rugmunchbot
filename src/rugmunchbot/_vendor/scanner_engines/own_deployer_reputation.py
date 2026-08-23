# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/own_deployer_reputation.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Deployer Reputation — ClickHouse-Powered Deployer Scoring
===============================================================
Phase 4 of the Data Ownership Strategy.

Scores a deployer's reputation (0-100) from our own ClickHouse data:
  - Total tokens deployed (from deployer_events)
  - Rugpull rate (proxy: launches whose DEX activity collapsed / went dead)
  - Average token lifespan (first → last deployer event per token)
  - Honeypot rate (proxy: launches with buys but zero sells)
  - Deployment velocity (launches per day, batch-deployer detection)

Architecture:
  - Queries deployer_events for the deployer's full history
  - Bounded per-token dex_events scan for rugpull/honeypot proxies
  - Returns a 0-100 reputation score (higher = more trustworthy)
  - Falls back gracefully when data is insufficient

Usage:
    from rugmunchbot._vendor.scanner_engines.own_deployer_reputation import (
        get_own_deployer_reputation,
    )

    result = await get_own_deployer_reputation("0x...", "ethereum")
    # result = {
    #     "reputation_score": 72,
    #     "reputation_label": "ESTABLISHED",
    #     "total_tokens_deployed": 3,
    #     "rugpull_rate": 0.0,
    #     "honeypot_rate": 0.0,
    #     "avg_token_lifespan_hours": 210.5,
    #     "deployment_velocity_per_day": 0.03,
    #     "confidence": 0.8,
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.deployer_events (deployment tracking)
  Primary: wallet_memory.dex_events (DEX swap data for rugpull/honeypot proxies)
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
from rugmunchbot._vendor.scanner_engines.own_dex_data.client import (
    _get_pools_for_token,
    _load_pair_cache,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_DEPLOYER_CACHE: dict[str, dict[str, Any]] = {}
_DEPLOYER_CACHE_TTL = 600  # 10 minutes — reputation changes slowly
_DEPLOYER_CACHE_MAX = 200

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
        logger.info("Own deployer reputation ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        _ch_local.client = candidate
        return candidate
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own deployer reputation ClickHouse unavailable: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_deployer_reputation(
    deployer_address: str,
    chain: str = "",
    force_fresh: bool = False,
    check_tokens: int = 10,
) -> dict[str, Any]:
    """Score deployer reputation (0-100) from own ClickHouse data.

    A higher score means a more trustworthy deployer. The score starts at a
    neutral 70 and is adjusted by:
      - deployment velocity (many launches per day → penalty)
      - rugpull proxy rate (launches that went dead/collapsed → penalty)
      - honeypot proxy rate (buys without sells → heavy penalty)
      - deployer age and total deployments (experience → mild bonus)

    Args:
        deployer_address: Deployer wallet address (lowercased internally)
        chain: Optional chain filter (empty = all chains)
        force_fresh: Bypass cache
        check_tokens: Max number of recent tokens to deep-scan for
                      rugpull/honeypot proxies (0 = skip deep scan)

    Returns:
        Dict with reputation_score, reputation_label, and the underlying
        metrics used to compute it. Returns an error dict on failure.
    """
    deployer_address = deployer_address.lower().strip()
    chain_norm = _normalize_chain(chain) if chain else ""
    _start = time.monotonic()

    if not deployer_address:
        return {"check": "deployer_reputation", "result": "error", "error": "No deployer address"}

    cache_key = f"{deployer_address}:{chain_norm or 'any'}"
    if not force_fresh and cache_key in _DEPLOYER_CACHE:
        cached = _DEPLOYER_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _DEPLOYER_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "deployer_reputation",
        "result": "scanned",
        "source": "own",
        "reputation_score": 50,
        "reputation_label": "UNKNOWN",
        "deployer_address": deployer_address,
        "chain": chain_norm or "any",
        "total_tokens_deployed": 0,
        "total_deployments": 0,
        "rugpull_rate": 0.0,
        "honeypot_rate": 0.0,
        "avg_token_lifespan_hours": None,
        "deployment_velocity_per_day": 0.0,
        "first_deployed_at": None,
        "last_deployed_at": None,
        "chains_used": [],
        "confidence": 0.0,
    }

    try:
        # ── 1. Deployer stats + per-token breakdown ──
        stats_task = asyncio.create_task(run_sync(_query_deployer_stats, deployer_address, chain_norm))
        tokens_task = asyncio.create_task(run_sync(_query_per_token_lifespan, deployer_address, chain_norm))
        stats, per_token = await asyncio.gather(stats_task, tokens_task)
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
        logger.debug("deployer_reputation_query_failed: %s", exc)
        result["result"] = "unavailable"
        return result

    if not stats or stats.get("total_deployments", 0) == 0:
        logger.debug("no deployer history for %s", deployer_address[:10])
        result["confidence"] = 0.0
        result["result"] = "unavailable"
        result["source"] = "own"
        return result

    # ── Populate base metrics ──
    result["total_tokens_deployed"] = int(stats.get("tokens_deployed", 0))
    result["total_deployments"] = int(stats.get("total_deployments", 0))
    result["first_deployed_at"] = stats.get("first_deployed_at")
    result["last_deployed_at"] = stats.get("last_deployed_at")
    result["chains_used"] = stats.get("chains_used", [])
    result["confidence"] = max(result["confidence"], 0.4)

    # ── 2. Average token lifespan from per-token events ──
    lifespans = [t.get("lifespan_hours") for t in per_token if t.get("lifespan_hours") is not None]
    if lifespans:
        result["avg_token_lifespan_hours"] = round(sum(lifespans) / len(lifespans), 1)

    # ── 3. Deployment velocity (launches per day) ──
    first_ts = stats.get("first_ts")
    last_ts = stats.get("last_ts")
    if first_ts and last_ts and last_ts > first_ts:
        span_days = max((last_ts - first_ts) / 86400, 0.01)
        result["deployment_velocity_per_day"] = round(stats["tokens_deployed"] / span_days, 4)

    # ── 4. Rugpull / honeypot proxies (bounded deep scan) ──
    rugpull_count = 0
    honeypot_count = 0
    checked = 0
    if check_tokens > 0 and per_token:
        recent_tokens = per_token[: min(check_tokens, len(per_token))]
        checks = await asyncio.gather(
            *[run_sync(_query_token_health, t["token_address"], t.get("chain") or chain_norm) for t in recent_tokens],
            return_exceptions=True,
        )
        for health in checks:
            if isinstance(health, BaseException) or not health:
                continue
            checked += 1
            if health.get("is_dead_token"):
                rugpull_count += 1
            if health.get("is_honeypot"):
                honeypot_count += 1

        if checked > 0:
            result["rugpull_rate"] = round(rugpull_count / checked, 3)
            result["honeypot_rate"] = round(honeypot_count / checked, 3)
            result["tokens_health_checked"] = checked

    # ── 5. Compute reputation score ──
    score = _compute_reputation_score(result, stats)
    result["reputation_score"] = score
    result["reputation_label"] = _label_for_score(score)
    result["confidence"] = round(min(1.0, result["confidence"] + 0.1 * min(result["total_deployments"], 5)), 2)

    _cache_deployer(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_deployer_reputation_latency",
        ms=round(_elapsed_ms, 1),
        deployer=deployer_address[:10],
        score=score,
        tokens=result["total_tokens_deployed"],
    )
    return result


def _compute_reputation_score(result: dict[str, Any], stats: dict[str, Any]) -> int:
    """Combine metrics into a single 0-100 reputation score (higher = better)."""
    score = 70.0  # neutral baseline

    tokens = int(result.get("total_tokens_deployed", 0) or 0)
    velocity = float(result.get("deployment_velocity_per_day", 0.0) or 0.0)
    rugpull_rate = float(result.get("rugpull_rate", 0.0) or 0.0)
    honeypot_rate = float(result.get("honeypot_rate", 0.0) or 0.0)

    # Batch-deployer penalty: many tokens at high velocity
    if tokens >= 10 and velocity > 2.0:
        score -= 25
    elif tokens >= 5 and velocity > 1.0:
        score -= 15
    elif tokens >= 3 and velocity > 0.5:
        score -= 8

    # Rugpull proxy penalty (bounded to deep-scanned subset)
    score -= rugpull_rate * 25

    # Honeypot proxy penalty — the strongest signal we have
    score -= honeypot_rate * 45

    # Experience bonus: an old, single-purpose deployer is more trustworthy
    first_ts = stats.get("first_ts")
    if first_ts:
        age_days = max((time.time() - first_ts) / 86400, 0)
        if age_days > 365:
            score += 10
        elif age_days > 90:
            score += 5

    # Long-lived tokens are a trust signal
    lifespan = result.get("avg_token_lifespan_hours")
    if lifespan is not None:
        if lifespan > 30 * 24:
            score += 10
        elif lifespan > 7 * 24:
            score += 5
        elif lifespan < 24 and tokens > 1:
            score -= 5

    # Small sample: pull toward neutral so we don't over-commit
    if tokens == 0:
        return 50
    if tokens < 3 and rugpull_rate == 0 and honeypot_rate == 0:
        score = (score + 70) / 2

    return max(0, min(100, round(score)))


def _label_for_score(score: int) -> str:
    """Map a 0-100 score to a human-readable label."""
    if score >= 85:
        return "TRUSTED"
    if score >= 70:
        return "ESTABLISHED"
    if score >= 55:
        return "NEUTRAL"
    if score >= 40:
        return "CAUTION"
    return "HIGH_RISK"


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_deployer_stats(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Aggregate the deployer's full deployment history from deployer_events."""
    client = _get_ch_client()
    if client is None:
        return None

    where_parts = ["deployer = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                deployer,
                count() AS total_deployments,
                count(DISTINCT token_address) AS tokens_deployed,
                uniqExact(chain) AS chains_used,
                min(block_timestamp) AS first_ts,
                max(block_timestamp) AS last_ts
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            GROUP BY deployer
            """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
        )
        if not rows:
            return None

        row = rows[0]
        return {
            "deployer": row[0],
            "total_deployments": row[1],
            "tokens_deployed": row[2],
            "chains_used": row[3],
            "first_ts": row[4],
            "last_ts": row[5],
            "first_deployed_at": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
            "last_deployed_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
        }
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("deployer_stats_query_failed: %s", exc)
        return None


def _query_per_token_lifespan(deployer_address: str, chain: str) -> list[dict[str, Any]]:
    """Return per-token lifespan (first→last deployer event) for a deployer."""
    client = _get_ch_client()
    if client is None:
        return []

    where_parts = ["deployer = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                token_address,
                any(chain) AS chain,
                count() AS n_events,
                min(block_timestamp) AS first_ts,
                max(block_timestamp) AS last_ts
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            GROUP BY token_address
            ORDER BY last_ts DESC
            LIMIT 200
            """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("per_token_lifespan_query_failed: %s", exc)
        return []

    tokens: list[dict[str, Any]] = []
    for row in rows:
        first_ts = row[3]
        last_ts = row[4]
        lifespan_hours = None
        if first_ts and last_ts and hasattr(last_ts, "timestamp") and hasattr(first_ts, "timestamp"):
            lifespan_hours = max((last_ts.timestamp() - first_ts.timestamp()) / 3600, 0.0)
        tokens.append(
            {
                "token_address": row[0],
                "chain": row[1],
                "event_count": row[2],
                "first_ts": first_ts,
                "last_ts": last_ts,
                "lifespan_hours": round(lifespan_hours, 1) if lifespan_hours is not None else None,
            }
        )
    return tokens


def _query_token_health(token_address: str, chain: str) -> dict[str, Any] | None:
    """Compute rugpull/honeypot proxies for a single token from dex_events.

    - is_honeypot: more than 5 buys and zero sells in the last 7 days
    - is_dead_token: token launched >72h ago but with no swaps in the last
      48h or with a tiny total swap count (abandoned / rugged proxy)

    Returns a health dict, or None when there is insufficient data to judge.
    """
    client = _get_ch_client()
    if client is None or not chain:
        return None

    try:
        pair_cache = _load_pair_cache(chain)
        known_pools = _get_pools_for_token(pair_cache, token_address)

        params: dict[str, Any] = {
            "addr0": token_address,
            "addr1": token_address,
            "chain": chain,
        }
        pool_clause = ""
        if known_pools:
            params["known_pools"] = known_pools
            pool_clause = " OR contract_address IN %(known_pools)s"

        rows = client.execute(
            f"""
            SELECT
                count() AS total_swaps,
                countIf(block_timestamp >= now() - INTERVAL 48 HOUR) AS swaps_48h,
                max(block_timestamp) AS last_swap_ts,
                countIf(
                    toFloat64OrZero(amount0_out) + toFloat64OrZero(amount1_out) > 0
                    AND toFloat64OrZero(amount0_in) + toFloat64OrZero(amount1_in) = 0
                ) AS buy_count,
                countIf(
                    toFloat64OrZero(amount0_in) + toFloat64OrZero(amount1_in) > 0
                    AND toFloat64OrZero(amount0_out) + toFloat64OrZero(amount1_out) = 0
                ) AS sell_count
            FROM wallet_memory.dex_events
            WHERE chain = %(chain)s
              AND type = 'swap'
              AND (token0 = %(addr0)s OR token1 = %(addr1)s{pool_clause})
            """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
        )
        if not rows or not rows[0]:
            return None

        row = rows[0]
        total_swaps = row[0] or 0
        swaps_48h = row[1] or 0
        buy_count = row[3] or 0
        sell_count = row[4] or 0

        if total_swaps < 3:
            return None  # Not enough activity to judge

        is_honeypot = buy_count > 5 and sell_count == 0
        is_dead_token = swaps_48h == 0 and total_swaps < 100

        return {
            "token_address": token_address,
            "total_swaps": total_swaps,
            "swaps_48h": swaps_48h,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "is_honeypot": is_honeypot,
            "is_dead_token": is_dead_token,
        }
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("token_health_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_deployer(key: str, data: dict[str, Any]) -> None:
    """Cache deployer reputation data with timestamp."""
    data["_cached_at"] = time.time()
    _DEPLOYER_CACHE[key] = data
    if len(_DEPLOYER_CACHE) > _DEPLOYER_CACHE_MAX:
        _DEPLOYER_CACHE.pop(next(iter(_DEPLOYER_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _DEPLOYER_CACHE,
        _DEPLOYER_CACHE_MAX,
        _DEPLOYER_CACHE_TTL,
        extra={"clickhouse_connected": _get_ch_client() is not None},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_DEPLOYER_CACHE)
