# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/deployer_profiles.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Repeat Deployer Profiles — launch history, rug rate, performance tracking
==========================================================================
Identifies *repeat deployers* (wallets that launched more than one token)
from our own ClickHouse data and builds a track-record profile for each:

  - total tokens launched / total deployments
  - avg liquidity at 24h (proxy: DEX volume USD in the first 24h per token)
  - rugged % (proxy: tokens old enough to judge whose post-72h DEX volume
    stayed below $1K, or that went dark after 72h)
  - still active % (tokens with DEX swaps in the last 48h)
  - honeypot % (proxy: >5 buys and zero sells)
  - avg token lifespan (days)

The label format is:

    "Repeat deployer: 15 tokens, 40% rugged, avg life 3 days"

Storage:
  - ClickHouse ``wallet_memory.deployer_profiles`` (durable, refreshed by
    ``refresh_deployer_profiles()`` — ``ReplacingMergeTree(computed_at)``).
  - Redis key ``deployer_profile:{chain|any}:{deployer}`` with a 6h TTL for
    fast scanner-path lookups.
  - In-process dict cache (10 min) mirroring the other own-data engines.

Scanner integration:
  - ``get_deployer_profile()`` is the fast path used by
    ``app.domains.scanner.modules.deployer._get_deployer_info``: Redis →
    ClickHouse table → on-demand compute (then persisted).
  - The scanner injects ``repeat_deployer``, ``deployer_profile`` and
    ``deployer_label`` into the scan module result and emits the
    ``REPEAT_DEPLOYER`` risk flag (see service/scan.py).

Usage:
    from rugmunchbot._vendor.scanner_engines.deployer_profiles import (
        get_deployer_profile,
        refresh_deployer_profiles,
        build_deployer_label,
    )

    profile = await get_deployer_profile("0x...", "base")
    summary = await refresh_deployer_profiles(chain="base")

CLI (periodic sweep):
    python -m app.domains.scanner.engines.deployer_profiles --chain base --limit 500
    python -m app.domains.scanner.engines.deployer_profiles --json

All queries are bounded (max_execution_time), parameter-bound, and degrade
gracefully to ``None``/empty when ClickHouse or Redis is unavailable.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.lite_chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_CACHE_PREFIX = "deployer_profile"
REDIS_TTL_SECONDS = 6 * 3600  # 6 hours — track record changes slowly
_PROFILE_CACHE: dict[str, dict[str, Any]] = {}
_PROFILE_CACHE_TTL = 600  # 10 minutes in-process mirror
_PROFILE_CACHE_MAX = 200

# ── Thresholds ────────────────────────────────────────────────────────────
MIN_DEPLOYMENTS = 2  # a "repeat deployer" launched >1 token
RUG_LIQUIDITY_USD = 1_000.0  # post-72h volume below $1K ⇒ rugged proxy
HONEYPOT_MIN_BUYS = 5  # >5 buys and zero sells ⇒ honeypot proxy
ACTIVE_WINDOW_HOURS = 48  # swaps in last 48h ⇒ still active
MATURE_HOURS = 72  # token must be 72h old before we judge ruggedness
MAX_TOKENS_PER_DEPLOYER = 50  # cap on per-token health checks per deployer
_QUERY_TIMEOUT_SECONDS = 10

# ── Table DDL (durable store) ─────────────────────────────────────────────
DEPLOYER_PROFILES_TABLE = "deployer_profiles"

DEPLOYER_PROFILES_DDL = f"""
CREATE TABLE IF NOT EXISTS {DEPLOYER_PROFILES_TABLE} (
    deployer_address     String,
    chain_id             LowCardinality(String) DEFAULT '',
    total_tokens_launched UInt32 DEFAULT 0,
    total_deployments    UInt32 DEFAULT 0,
    avg_liquidity_24h    Float64 DEFAULT 0,
    rugged_pct           Float32 DEFAULT 0,
    active_pct           Float32 DEFAULT 0,
    honeypot_pct         Float32 DEFAULT 0,
    avg_lifespan_days    Float32 DEFAULT 0,
    chains_used          Array(String) DEFAULT [],
    first_seen           DateTime,
    last_seen            DateTime,
    computed_at          DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (deployer_address, chain_id)
"""


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def detect_repeat_deployers(
    chain: str = "",
    min_deployments: int = MIN_DEPLOYMENTS,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Find repeat deployers: wallets with >1 deployment event in deployer_events.

    Args:
        chain: Optional chain filter (empty = all chains).
        min_deployments: Minimum deployment count to qualify (default 2).
        limit: Max results.

    Returns:
        List of {deployer, total_deployments, tokens_launched, first_seen,
                 last_seen, chains_used} ordered by deployments DESC.
    """
    client = _get_ch_client()
    if client is None:
        return []

    where_clause = "1=1"
    params: dict[str, Any] = {"min_deployments": min_deployments, "limit": limit}
    if chain:
        where_clause = "chain = %(chain)s"
        params["chain"] = _normalize_chain(chain)

    query = f"""
    SELECT
        deployer,
        count() AS total_deployments,
        uniqExact(token_address) AS tokens_launched,
        min(block_timestamp) AS first_seen,
        max(block_timestamp) AS last_seen,
        groupUniqArray(chain) AS chains_used
    FROM wallet_memory.deployer_events
    WHERE {where_clause}
    GROUP BY deployer
    HAVING count() > %(min_deployments)s
    ORDER BY total_deployments DESC, tokens_launched DESC
    LIMIT %(limit)s
    """  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders

    try:
        rows = client.execute(
            query,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("detect_repeat_deployers_failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for row in rows:
        deployer, deployments, tokens, first_ts, last_ts, chains = row
        results.append(
            {
                "deployer": deployer.lower(),
                "total_deployments": int(deployments or 0),
                "tokens_launched": int(tokens or 0),
                "first_seen": first_ts.isoformat() if hasattr(first_ts, "isoformat") else str(first_ts or ""),
                "last_seen": last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts or ""),
                "chains_used": sorted({str(c) for c in (chains or [])}),
            }
        )
    return results


async def get_deployer_profile(
    deployer_address: str,
    chain: str = "",
    force_fresh: bool = False,
) -> dict[str, Any] | None:
    """Return the repeat-deployer track record for a wallet (fast path).

    Lookup order: in-process cache → Redis (6h TTL) → ClickHouse
    ``deployer_profiles`` table → on-demand compute (persisted afterwards).

    Returns ``None`` when the wallet is not a repeat deployer or when there
    is no data (graceful degradation).
    """
    deployer = (deployer_address or "").lower().strip()
    if not deployer:
        return None
    chain_norm = _normalize_chain(chain) if chain else ""
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm or 'any'}:{deployer}"

    # ── 1. In-process mirror cache ──
    if not force_fresh and cache_key in _PROFILE_CACHE:
        entry = _PROFILE_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _PROFILE_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 2. Redis cache ──
    if not force_fresh:
        cached = await redis_get(cache_key)
        if cached is not None:
            cached_dict: dict[str, Any] = cached
            _remember(cache_key, cached_dict)
            return cached_dict

    # ── 3. ClickHouse table ──
    row = await run_sync(_query_profile_row, deployer, chain_norm)
    if row is not None:
        profile = _row_to_profile(row)
        if profile is not None:
            _remember(cache_key, profile)
            await redis_set(cache_key, profile, REDIS_TTL_SECONDS)
            return profile

    # ── 4. On-demand compute (then persist) ──
    profile = await compute_deployer_profile(deployer, chain_norm)
    if profile is not None:
        _remember(cache_key, profile)
        await redis_set(cache_key, profile, REDIS_TTL_SECONDS)
        await run_sync(_upsert_profile_row, profile)
    return profile


async def compute_deployer_profile(
    deployer_address: str,
    chain: str = "",
    check_tokens: int = MAX_TOKENS_PER_DEPLOYER,
) -> dict[str, Any] | None:
    """Compute a full repeat-deployer track record from ClickHouse.

    Returns ``None`` when the deployer has <=1 deployment (not a repeat
    deployer) or when there is no deployer history.
    """
    deployer = (deployer_address or "").lower().strip()
    if not deployer:
        return None
    chain_norm = _normalize_chain(chain) if chain else ""
    _start = time.monotonic()

    # ── Round 1 (parallel): deployer stats + token list ──
    stats = await run_sync(_query_deployer_stats, deployer, chain_norm)
    if not stats or stats.get("total_deployments", 0) <= 1:
        logger.debug("not_a_repeat_deployer", deployer=deployer[:10])
        return None
    tokens = await run_sync(_query_deployer_tokens, deployer, chain_norm, check_tokens)

    # ── Round 2: per-token DEX lifecycle (single batched query) ──
    lifecycle = await run_sync(_query_batch_token_lifecycle, deployer, tokens, chain_norm)
    token_stats = [lifecycle.get(t["token_address"]) for t in tokens]

    # ── Aggregate metrics ──
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    total = len(tokens)
    rugged = active = honeypot = matured = 0
    lifespans: list[float] = []
    liquidity_24h: list[float] = []

    for tok, health in zip(tokens, token_stats, strict=False):
        if isinstance(health, BaseException) or not health:
            # No DEX activity on record → synthesize a dead token so mature
            # launches count as rugged instead of being silently dropped.
            health = {
                "swap_count": 0,
                "swaps_48h": 0,
                "last_swap_ts": None,
                "volume_24h": 0.0,
                "volume_after_72h": 0.0,
                "swaps_after_72h": 0,
                "buy_count": 0,
                "sell_count": 0,
            }
        deployed_at = tok.get("deployed_at")
        if deployed_at is None:
            continue

        # Lifespan: last swap - deploy time (hours → days)
        last_swap = health.get("last_swap_ts")
        if last_swap is not None and hasattr(last_swap, "timestamp"):
            lifespan_h = max((last_swap - deployed_at).total_seconds() / 3600, 0.0)
            lifespans.append(lifespan_h)

        # Liquidity proxy: volume in first 24h after deploy
        vol_24h = health.get("volume_24h")
        if isinstance(vol_24h, (int, float)) and vol_24h > 0:
            liquidity_24h.append(float(vol_24h))

        # Maturity: only judge tokens old enough (72h+)
        if deployed_at + timedelta(hours=MATURE_HOURS) <= now_naive:
            matured += 1
            swaps_after_72h = int(health.get("swaps_after_72h", 0) or 0)
            vol_after_72h = health.get("volume_after_72h")
            # Rugged proxy (chain-agnostic): no DEX swaps after 72h is the
            # primary signal; the <$1K volume check only applies on chains
            # where volume_usd is actually populated (e.g. base/arbitrum).
            if swaps_after_72h == 0 or (isinstance(vol_after_72h, (int, float)) and vol_after_72h < RUG_LIQUIDITY_USD):
                rugged += 1

        # Still active: swaps in last 48h
        if int(health.get("swaps_48h", 0) or 0) > 0:
            active += 1

        # Honeypot proxy: >5 buys and zero sells
        if int(health.get("buy_count", 0) or 0) > HONEYPOT_MIN_BUYS and int(health.get("sell_count", 0) or 0) == 0:
            honeypot += 1

    rugged_pct = round(rugged / matured, 3) if matured else 0.0
    active_pct = round(active / total, 3) if total else 0.0
    honeypot_pct = round(honeypot / total, 3) if total else 0.0
    avg_liquidity_24h = round(sum(liquidity_24h) / len(liquidity_24h), 2) if liquidity_24h else 0.0
    avg_lifespan_days = round(sum(lifespans) / len(lifespans) / 24, 2) if lifespans else None

    profile: dict[str, Any] = {
        "check": "deployer_profiles",
        "result": "scanned",
        "deployer": deployer,
        "chain": chain_norm or "any",
        "is_repeat_deployer": True,
        "total_tokens_launched": int(stats.get("tokens_launched", total) or total),
        "total_deployments": int(stats.get("total_deployments", 0) or 0),
        "avg_liquidity_24h": avg_liquidity_24h,
        "rugged_pct": rugged_pct,
        "active_pct": active_pct,
        "honeypot_pct": honeypot_pct,
        "avg_lifespan_days": avg_lifespan_days,
        "first_seen": stats.get("first_seen", ""),
        "last_seen": stats.get("last_seen", ""),
        "chains_used": stats.get("chains_used", []),
        "tokens_checked": total,
        "confidence": round(min(1.0, 0.3 + 0.1 * min(total, 7)), 2),
    }
    profile["label"] = build_deployer_label(profile)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "deployer_profile_computed",
        deployer=deployer[:10],
        tokens=total,
        rugged_pct=rugged_pct,
        active_pct=active_pct,
        honeypot_pct=honeypot_pct,
        lifespan_days=avg_lifespan_days,
        ms=round(_elapsed_ms, 1),
    )
    return profile


def build_deployer_label(profile: dict[str, Any]) -> str:
    """Build the human-readable track-record label.

    Format: ``"Repeat deployer: 15 tokens, 40% rugged, avg life 3 days"``
    """
    tokens = int(profile.get("total_tokens_launched", 0) or 0)
    rugged_pct = round(float(profile.get("rugged_pct", 0.0) or 0.0) * 100)
    life_days = profile.get("avg_lifespan_days")
    if life_days is None:
        life_str = "unknown"
    elif life_days >= 1:
        life_str = f"{round(life_days)} days"
    else:
        life_str = f"{round(life_days * 24)} hours"
    return f"Repeat deployer: {tokens} tokens, {rugged_pct}% rugged, avg life {life_str}"


async def refresh_deployer_profiles(
    chain: str = "",
    min_deployments: int = MIN_DEPLOYMENTS,
    limit: int = 500,
    concurrency: int = 8,
) -> dict[str, Any]:
    """Compute + persist repeat-deployer profiles for the whole table.

    Runs detection, computes each profile (semaphore-limited), writes to
    ClickHouse ``deployer_profiles`` and refreshes the Redis cache.

    Returns a summary dict: {deployers_found, profiles_computed, stored,
    errors, duration_ms, chain}.
    """
    start = time.monotonic()
    deployers = await detect_repeat_deployers(chain=chain, min_deployments=min_deployments, limit=limit)
    if not deployers:
        return {
            "deployers_found": 0,
            "profiles_computed": 0,
            "stored": 0,
            "errors": [],
            "duration_ms": round((time.monotonic() - start) * 1000, 1),
            "chain": chain or "any",
        }

    sem = asyncio.Semaphore(max(1, concurrency))
    errors: list[str] = []
    profiles: list[dict[str, Any]] = []
    stored = 0

    async def _work(d: dict[str, Any]) -> None:
        nonlocal stored
        async with sem:
            try:
                profile = await compute_deployer_profile(
                    d["deployer"],
                    chain,
                    check_tokens=MAX_TOKENS_PER_DEPLOYER,
                )
                if profile is None:
                    return
                profiles.append(profile)
                cache_key = f"{REDIS_CACHE_PREFIX}:{profile['chain']}:{profile['deployer']}"
                _remember(cache_key, profile)
                await redis_set(cache_key, profile, REDIS_TTL_SECONDS)
                if await run_sync(_upsert_profile_row, profile):
                    stored += 1
            except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
                errors.append(f"{d['deployer'][:10]}: {str(exc)[:80]}")

    await asyncio.gather(*(_work(d) for d in deployers))

    logger.info(
        "deployer_profiles_refreshed",
        found=len(deployers),
        computed=len(profiles),
        stored=stored,
        errors=len(errors),
        duration_ms=round((time.monotonic() - start) * 1000, 1),
    )
    return {
        "deployers_found": len(deployers),
        "profiles_computed": len(profiles),
        "stored": stored,
        "errors": errors,
        "duration_ms": round((time.monotonic() - start) * 1000, 1),
        "chain": chain or "any",
    }


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _PROFILE_CACHE,
        _PROFILE_CACHE_MAX,
        _PROFILE_CACHE_TTL,
        extra={"clickhouse_connected": _get_ch_client() is not None},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_PROFILE_CACHE)


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_deployer_stats(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Aggregate deployment stats for one deployer from deployer_events."""
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
                count() AS total_deployments,
                uniqExact(token_address) AS tokens_launched,
                groupUniqArray(chain) AS chains_used,
                min(block_timestamp) AS first_ts,
                max(block_timestamp) AS last_ts
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("deployer_profile_stats_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None

    row = rows[0]
    first_ts = row[3]
    last_ts = row[4]
    return {
        "deployer": deployer_address,
        "total_deployments": int(row[0] or 0),
        "tokens_launched": int(row[1] or 0),
        "chains_used": sorted({str(c) for c in (row[2] or [])}),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "first_seen": first_ts.isoformat() if hasattr(first_ts, "isoformat") else str(first_ts or ""),
        "last_seen": last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts or ""),
    }


def _query_deployer_tokens(
    deployer_address: str,
    chain: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Return per-token deployment records for a deployer (bounded)."""
    client = _get_ch_client()
    if client is None:
        return []

    where_parts = ["deployer = %(deployer)s"]
    params: dict[str, Any] = {"deployer": deployer_address, "limit": limit}
    if chain:
        where_parts.append("chain = %(chain)s")
        params["chain"] = chain
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                token_address,
                any(chain) AS token_chain,
                min(block_timestamp) AS deployed_ts
            FROM wallet_memory.deployer_events
            WHERE {where_clause}
            GROUP BY token_address
            ORDER BY deployed_ts DESC
            LIMIT %(limit)s
            """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("deployer_profile_tokens_failed: %s", exc)
        return []

    tokens: list[dict[str, Any]] = []
    for row in rows:
        tokens.append(
            {
                "token_address": row[0].lower(),
                "chain": row[1],
                "deployed_at": row[2],
            }
        )
    return tokens


def _query_batch_token_lifecycle(
    deployer_address: str,
    tokens: list[dict[str, Any]],
    chain: str,
) -> dict[str, dict[str, Any]]:
    """Per-token DEX lifecycle from dex_events — ONE batched query per deployer.

    Joins the deployer's token list (with per-token deploy times) against
    dex_events so the 24h/72h windows are evaluated relative to each token's
    own launch. Returns a dict keyed by lowercased token address.

    Per-token fields:
        - swap_count, swaps_48h, last_swap_ts
        - volume_24h: DEX volume USD in [deploy, deploy+24h] (liquidity proxy)
        - volume_after_72h: DEX volume USD in [deploy+72h, now] (rug proxy)
        - swaps_after_72h: swap count in [deploy+72h, now] (chain-agnostic rug)
        - buy_count / sell_count (honeypot proxy)
    """
    client = _get_ch_client()
    if client is None or not tokens:
        return {}

    token_addrs = [t["token_address"] for t in tokens]
    # Historical dex_events have empty token0/token1; resolve via pair cache.
    pools: set[str] = set()
    try:
        from rugmunchbot._vendor.scanner_engines.own_dex_data.client import (
            _get_pools_for_token,
            _load_pair_cache,
        )

        for t in tokens:
            pair_cache = _load_pair_cache(t.get("chain") or chain or "")
            pools.update(_get_pools_for_token(pair_cache, t["token_address"]))
    except (ImportError, ValueError, OSError):
        pass

    params: dict[str, Any] = {
        "deployer": deployer_address,
        "tokens": tuple(token_addrs),
        "chain": chain,
        "pools": tuple(sorted(pools)) if pools else (),
    }
    pool_clause = " OR d.contract_address IN %(pools)s"

    query = f"""
    SELECT
        tok.token_address AS token_address,
        count(d.block_timestamp) AS total_swaps,
        countIf(d.block_timestamp >= now() - INTERVAL {ACTIVE_WINDOW_HOURS} HOUR) AS swaps_48h,
        max(d.block_timestamp) AS last_swap_ts,
        sumIf(
            d.volume_usd,
            d.block_timestamp >= tok.deployed_ts
            AND d.block_timestamp < tok.deployed_ts + INTERVAL 24 HOUR
        ) AS volume_24h,
        sumIf(
            d.volume_usd,
            d.block_timestamp >= tok.deployed_ts + INTERVAL {MATURE_HOURS} HOUR
        ) AS volume_after_72h,
        countIf(
            d.block_timestamp >= tok.deployed_ts + INTERVAL {MATURE_HOURS} HOUR
        ) AS swaps_after_72h,
        countIf(
            toFloat64OrZero(d.amount0_out) + toFloat64OrZero(d.amount1_out) > 0
            AND toFloat64OrZero(d.amount0_in) + toFloat64OrZero(d.amount1_in) = 0
        ) AS buy_count,
        countIf(
            toFloat64OrZero(d.amount0_in) + toFloat64OrZero(d.amount1_in) > 0
            AND toFloat64OrZero(d.amount0_out) + toFloat64OrZero(d.amount1_out) = 0
        ) AS sell_count
    FROM (
        SELECT
            token_address,
            any(chain) AS token_chain,
            min(block_timestamp) AS deployed_ts
        FROM wallet_memory.deployer_events
        WHERE deployer = %(deployer)s
          AND token_address IN %(tokens)s
        GROUP BY token_address
    ) AS tok
    LEFT JOIN wallet_memory.dex_events AS d
        ON d.chain = tok.token_chain
       AND d.type = 'swap'
       AND (d.token0 = tok.token_address OR d.token1 = tok.token_address{pool_clause})
    GROUP BY tok.token_address, tok.deployed_ts
    """  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders

    try:
        rows = client.execute(
            query,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("deployer_profile_batch_lifecycle_failed: %s", exc)
        return {}

    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        token_addr = row[0].lower()
        result[token_addr] = {
            "swap_count": int(row[1] or 0),
            "swaps_48h": int(row[2] or 0),
            "last_swap_ts": row[3],
            "volume_24h": float(row[4] or 0.0),
            "volume_after_72h": float(row[5] or 0.0),
            "swaps_after_72h": int(row[6] or 0),
            "buy_count": int(row[7] or 0),
            "sell_count": int(row[8] or 0),
        }
    return result


def _query_profile_row(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Read the most recent stored profile from ClickHouse (if any)."""
    client = _get_ch_client()
    if client is None:
        return None

    params: dict[str, Any] = {"deployer": deployer_address}
    chain_clause = ""
    if chain:
        chain_clause = "AND chain_id = %(chain)s"
        params["chain"] = chain

    try:
        rows = client.execute(
            f"""
            SELECT
                deployer_address, chain_id, total_tokens_launched, total_deployments,
                avg_liquidity_24h, rugged_pct, active_pct, honeypot_pct,
                avg_lifespan_days, chains_used, first_seen, last_seen
            FROM wallet_memory.{DEPLOYER_PROFILES_TABLE}
            WHERE deployer_address = %(deployer)s {chain_clause}
            ORDER BY computed_at DESC
            LIMIT 1
            """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("deployer_profile_row_failed: %s", exc)
        return None

    if not rows:
        return None
    return {"row": rows[0]}


def _row_to_profile(row_data: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a stored ClickHouse row into a profile dict."""
    row = row_data.get("row")
    if not row:
        return None
    deployer, chain_id, tokens, deployments, liq, rugged, active, honeypot, lifespan, chains, first, last = row
    if int(deployments or 0) <= 1:
        return None
    profile: dict[str, Any] = {
        "check": "deployer_profiles",
        "result": "scanned",
        "deployer": deployer,
        "chain": chain_id or "any",
        "is_repeat_deployer": True,
        "total_tokens_launched": int(tokens or 0),
        "total_deployments": int(deployments or 0),
        "avg_liquidity_24h": float(liq or 0.0),
        "rugged_pct": float(rugged or 0.0),
        "active_pct": float(active or 0.0),
        "honeypot_pct": float(honeypot or 0.0),
        "avg_lifespan_days": float(lifespan) if lifespan is not None else None,
        "chains_used": sorted({str(c) for c in (chains or [])}),
        "first_seen": first.isoformat() if hasattr(first, "isoformat") else str(first or ""),
        "last_seen": last.isoformat() if hasattr(last, "isoformat") else str(last or ""),
        "confidence": 0.8,  # stored rows were fully computed
    }
    profile["label"] = build_deployer_label(profile)
    return profile


def _upsert_profile_row(profile: dict[str, Any]) -> bool:
    """Insert/upsert a profile into ClickHouse deployer_profiles."""
    client = _get_ch_client()
    if client is None:
        return False

    try:
        client.execute(DEPLOYER_PROFILES_DDL)
        first_seen = _parse_dt(profile.get("first_seen"))
        last_seen = _parse_dt(profile.get("last_seen"))
        chain_id = "" if profile.get("chain") in ("", "any", None) else str(profile["chain"])
        client.execute(
            f"INSERT INTO wallet_memory.{DEPLOYER_PROFILES_TABLE} "
            "(deployer_address, chain_id, total_tokens_launched, total_deployments, "
            "avg_liquidity_24h, rugged_pct, active_pct, honeypot_pct, avg_lifespan_days, "
            "chains_used, first_seen, last_seen, computed_at) VALUES",
            [
                (
                    str(profile["deployer"]).lower(),
                    chain_id,
                    int(profile.get("total_tokens_launched", 0) or 0),
                    int(profile.get("total_deployments", 0) or 0),
                    float(profile.get("avg_liquidity_24h", 0.0) or 0.0),
                    float(profile.get("rugged_pct", 0.0) or 0.0),
                    float(profile.get("active_pct", 0.0) or 0.0),
                    float(profile.get("honeypot_pct", 0.0) or 0.0),
                    float(profile.get("avg_lifespan_days") or 0.0)
                    if profile.get("avg_lifespan_days") is not None
                    else 0.0,
                    [str(c) for c in profile.get("chains_used", [])],
                    first_seen,
                    last_seen,
                    datetime.now(UTC).replace(tzinfo=None),
                )
            ],
        )
        return True
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("deployer_profile_upsert_failed: %s", exc)
        return False


def _parse_dt(value: Any) -> datetime:
    """Parse an ISO/naive datetime string back to a naive datetime."""
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    if not value:
        return now_naive
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (ValueError, TypeError):
        return now_naive


def _remember(key: str, value: dict[str, Any]) -> None:
    """Store in the in-process mirror cache."""
    value = dict(value)
    value["_cached_at"] = time.time()
    _PROFILE_CACHE[key] = value
    if len(_PROFILE_CACHE) > _PROFILE_CACHE_MAX:
        _PROFILE_CACHE.pop(next(iter(_PROFILE_CACHE)), None)


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════
# Periodic sweep (systemd timer / cron):
#   python -m app.domains.scanner.engines.deployer_profiles --chain base
#   python -m app.domains.scanner.engines.deployer_profiles --chain all --limit 1000 --json


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: refresh repeat-deployer profiles."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Repeat deployer detection — launch history, rug rate, performance tracking",
    )
    parser.add_argument("--chain", default="", help="Chain filter (empty = all chains)")
    parser.add_argument("--min-deployments", type=int, default=MIN_DEPLOYMENTS, help="Min deployments to qualify")
    parser.add_argument("--limit", type=int, default=500, help="Max deployers to process")
    parser.add_argument("--concurrency", type=int, default=8, help="Parallel profile computations")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary instead of structured logs")
    args = parser.parse_args(argv)

    try:
        summary = asyncio.run(
            refresh_deployer_profiles(
                chain=args.chain,
                min_deployments=args.min_deployments,
                limit=args.limit,
                concurrency=args.concurrency,
            )
        )
    except KeyboardInterrupt:
        logger.info("deployer_profiles_interrupted")
        return 130
    except Exception as exc:  # pragma: no cover - defensive CLI guard
        logger.error("deployer_profiles_failed", error=str(exc))
        print(json.dumps({"status": "error", "error": str(exc)}, default=str))
        return 1

    if args.json:
        print(json.dumps(summary, default=str))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
