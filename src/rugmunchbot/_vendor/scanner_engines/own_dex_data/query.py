# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_dex_data/query.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""ClickHouse queries — dex_events queries, main API, fallback, freshness, price."""

from __future__ import annotations

import time
from typing import Any, cast

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines.own_dex_data.client import (
    _DEX_CACHE,
    _DEX_CACHE_MAX,
    _get_ch_client,
    _get_pools_for_token,
    _load_pair_cache,
    _normalize_chain,
    _run_sync,
)
from rugmunchbot._vendor.scanner_engines.own_dex_data.format import _build_pairs, _parse_decimal

logger = get_logger(__name__)


async def get_own_dex_data(
    token_address: str,
    chain: str,
    hours_24: int = 24,
) -> dict[str, Any]:
    """Get DEX trading data from ClickHouse — Drop-in DexScreener replacement.

    Returns a DexScreener-compatible dict structure:
    {
        "pairs": [...],
        "source": "own",  # or "fallback" if DexScreener was used
        "metrics": {
            "total_volume_24h": float,
            "total_liquidity_usd": float,
            "swap_count_24h": int,
            "unique_pools": int,
        }
    }
    """
    _start = time.monotonic()

    # Check process-level cache first
    cache_key = f"{token_address.lower()}:{chain}:{hours_24}"
    if cache_key in _DEX_CACHE:
        cached = _DEX_CACHE[cache_key]
        if cached:
            return cached

    token_address = token_address.lower()
    chain = _normalize_chain(chain)

    # Query ClickHouse for dex_events
    raw_swaps = await _query_dex_swaps(token_address, chain, hours_24)

    # If insufficient data, fall back to DexScreener API
    if len(raw_swaps) < 3:
        fallback = await _fallback_to_dexscreener(token_address)
        if fallback:
            _DEX_CACHE[cache_key] = fallback
            if len(_DEX_CACHE) > _DEX_CACHE_MAX:
                _DEX_CACHE.pop(next(iter(_DEX_CACHE)), None)
            _elapsed_ms = (time.monotonic() - _start) * 1000
            logger.info(
                "own_dex_data_latency",
                ms=round(_elapsed_ms, 1),
                swaps=len(fallback.get("pairs", [])),
                chain=chain,
                source="fallback",
            )
            return fallback
        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "own_dex_data_latency",
            ms=round(_elapsed_ms, 1),
            swaps=0,
            chain=chain,
            source="unavailable",
        )
        return {
            "pairs": [],
            "source": "unavailable",
            "metrics": {"total_volume_24h": 0, "total_liquidity_usd": 0, "swap_count_24h": 0, "unique_pools": 0},
            "error": "No DEX data available from ClickHouse or DexScreener",
        }

    # Build DexScreener-compatible pairs from our data
    pairs = _build_pairs(raw_swaps, token_address, chain)

    # Compute aggregate metrics
    total_volume = sum(p["volume"]["h24"] for p in pairs)
    total_liquidity = sum(p["liquidity"]["usd"] for p in pairs)
    total_swaps = sum(p["txns"]["h24"]["buys"] + p["txns"]["h24"]["sells"] for p in pairs)
    unique_pools = len({p["pairAddress"] for p in pairs})

    result: dict[str, Any] = {
        "pairs": pairs,
        "source": "own",
        "metrics": {
            "total_volume_24h": round(total_volume, 2),
            "total_liquidity_usd": round(total_liquidity, 2),
            "swap_count_24h": total_swaps,
            "unique_pools": unique_pools,
        },
    }

    # Cache (with LRU eviction)
    _DEX_CACHE[cache_key] = result
    if len(_DEX_CACHE) > _DEX_CACHE_MAX:
        _DEX_CACHE.pop(next(iter(_DEX_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_dex_data_latency",
        ms=round(_elapsed_ms, 1),
        swaps=len(pairs),
        chain=chain,
        source="own",
    )
    return result


async def _query_dex_swaps(
    token_address: str,
    chain: str,
    hours_back: int = 24,
) -> list[dict[str, Any]]:
    """Query dex_events for swap events involving this token.

    Returns list of raw swap dicts ordered by block_timestamp DESC.
    """

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        pair_cache = _load_pair_cache(chain)
        known_pools = _get_pools_for_token(pair_cache, token_address)

        params: dict[str, Any] = {
            "addr0": token_address,
            "addr1": token_address,
            "chain": chain,
            "hours": hours_back,
        }

        where_conditions = [
            "chain = %(chain)s",
            "type = 'swap'",
            "block_timestamp >= now() - INTERVAL %(hours)s HOUR",
            "(token0 = %(addr0)s OR token1 = %(addr1)s)",
        ]

        if known_pools:
            params["known_pools"] = known_pools
            where_conditions[-1] = "(token0 = %(addr0)s OR token1 = %(addr1)s OR contract_address IN %(known_pools)s)"

        where_clause = " AND ".join(where_conditions)

        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    f"""
                SELECT
                    contract_address, dex, version,
                    token0, token1,
                    amount0_in, amount1_in, amount0_out, amount1_out,
                    amount0, amount1,
                    block_timestamp, tx_hash, log_index,
                    type, sender, recipient, whale
                FROM wallet_memory.dex_events
                WHERE {where_clause}
                ORDER BY block_timestamp DESC
                LIMIT 500
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                    params,
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("dex_events query failed for %s: %s", token_address, exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return []

    swaps: list[dict[str, Any]] = []
    for row in rows:
        try:
            swaps.append(
                {
                    "contract_address": row[0],
                    "dex": row[1],
                    "version": row[2],
                    "token0": row[3],
                    "token1": row[4],
                    "amount0_in": _parse_decimal(row[5]),
                    "amount1_in": _parse_decimal(row[6]),
                    "amount0_out": _parse_decimal(row[7]),
                    "amount1_out": _parse_decimal(row[8]),
                    "amount0": _parse_decimal(row[9]),
                    "amount1": _parse_decimal(row[10]),
                    "block_timestamp": row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11]),
                    "tx_hash": row[12],
                    "log_index": row[13],
                    "type": row[14],
                    "sender": row[15],
                    "recipient": row[16],
                    "whale": bool(row[17]) if len(row) > 17 else False,
                    "timestamp": row[11].timestamp() if hasattr(row[11], "timestamp") else time.time(),
                }
            )
        except (ValueError, TypeError, IndexError) as e:
            logger.debug("Failed to parse dex_events row: %s", e)
            continue

    return swaps


async def _query_pool_stats(
    pool_address: str,
    chain: str,
    hours_back: int = 24,
) -> dict[str, Any] | None:
    """Query dex_events aggregated by pool for quick volume/liquidity stats."""

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "pool": pool_address,
            "chain": chain,
            "hours": hours_back,
        }

        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    """
                SELECT
                    contract_address,
                    count() AS total_swaps,
                    sum(toFloat64(amount0_in) + toFloat64(amount0_out)) AS vol0,
                    sum(toFloat64(amount1_in) + toFloat64(amount1_out)) AS vol1,
                    dex, version, token0, token1
                FROM wallet_memory.dex_events
                WHERE contract_address = %(pool)s
                  AND chain = %(chain)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
                GROUP BY contract_address, dex, version, token0, token1
                """,
                    params,
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("pool_stats query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows or not rows[0]:
        return None

    row = rows[0]
    try:
        return {
            "pool_address": row[0],
            "total_swaps": row[1],
            "volume_token0": float(row[2] or 0),
            "volume_token1": float(row[3] or 0),
            "dex": row[4],
            "version": row[5],
            "token0": row[6],
            "token1": row[7],
        }
    except (ValueError, TypeError, IndexError):
        return None


async def _fallback_to_dexscreener(token_address: str) -> dict[str, Any] | None:
    """Fall back to DexScreener API when ClickHouse has insufficient data."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and data.get("pairs"):
                    data["source"] = "fallback"
                    data["metrics"] = {
                        "total_volume_24h": sum(float(p.get("volume", {}).get("h24", 0) or 0) for p in data["pairs"]),
                        "swap_count_24h": sum(
                            int(p.get("txns", {}).get("h24", {}).get("buys", 0) or 0)
                            + int(p.get("txns", {}).get("h24", {}).get("sells", 0) or 0)
                            for p in data["pairs"]
                        ),
                        "unique_pools": len(data["pairs"]),
                    }
                    logger.info("DexScreener fallback used for %s", token_address)
                    return cast("dict[str, Any]", data)
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError) as exc:
        logger.debug("DexScreener fallback failed for %s: %s", token_address, exc)

    return None


async def check_data_freshness(chain: str = "ethereum") -> dict[str, Any]:
    """Check how fresh the dex_events data is for a given chain.

    Returns:
        {
            "latest_block_timestamp": "2026-07-30T12:00:00",
            "age_minutes": 5.2,
            "event_count_24h": 12345,
            "healthy": True,
        }
    """

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    """
                SELECT
                    max(block_timestamp) AS latest_ts,
                    count() AS event_count,
                    max(block_number) AS latest_block
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND block_timestamp >= now() - INTERVAL 24 HOUR
                """,
                    {"chain": chain},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("freshness check failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows or not rows[0]:
        return {"healthy": False, "error": "Could not query data freshness"}

    row = rows[0]
    latest_ts = row[0]
    age_minutes = 0.0
    if latest_ts and hasattr(latest_ts, "timestamp"):
        age_minutes = (time.time() - latest_ts.timestamp()) / 60

    return {
        "chain": chain,
        "latest_block_timestamp": latest_ts.isoformat() if hasattr(latest_ts, "isoformat") else str(latest_ts),
        "age_minutes": round(age_minutes, 1),
        "event_count_24h": row[1],
        "latest_block": row[2],
        "healthy": age_minutes < 60,
    }


async def get_token_price_simple(
    token_address: str,
    chain: str,
    lookback_minutes: int = 60,
) -> dict[str, Any]:
    """Quick TWAP price from dex_events.

    Returns:
        {
            "price_usd": 0.001234,
            "confidence": 0.85,
            "swap_count": 42,
            "time_span_minutes": 55,
            "source": "own_dex_events",
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)

    swaps = await _query_dex_swaps(token_address, chain, lookback_minutes // 60 + 1)

    cutoff = time.time() - lookback_minutes * 60
    recent_swaps = [s for s in swaps if s["timestamp"] >= cutoff]

    if len(recent_swaps) < 2:
        return {
            "price_usd": 0.0,
            "confidence": 0.0,
            "swap_count": len(recent_swaps),
            "time_span_minutes": 0,
            "source": "insufficient_data",
        }

    prices_weighted: list[tuple[float, float]] = []
    for swap in recent_swaps:
        is_token0 = swap["token0"].lower() == token_address
        if is_token0:
            amt_out = float(swap["amount0_out"] or 0)
            paired_in = float(swap["amount1_in"] or 0)
            if amt_out > 0:
                price = (paired_in or 0) / amt_out
                if 0 < price < 1_000_000:
                    prices_weighted.append((price, amt_out))
        else:
            amt_out = float(swap["amount1_out"] or 0)
            paired_in = float(swap["amount0_in"] or 0)
            if amt_out > 0:
                price = (paired_in or 0) / amt_out
                if 0 < price < 1_000_000:
                    prices_weighted.append((price, amt_out))

    if not prices_weighted:
        return {
            "price_usd": 0.0,
            "confidence": 0.0,
            "swap_count": len(recent_swaps),
            "time_span_minutes": 0,
            "source": "no_valid_price",
        }

    total_weight = sum(w for _, w in prices_weighted)
    vwap = sum(p * w for p, w in prices_weighted) / total_weight if total_weight > 0 else 0

    prices_only = [p for p, _ in prices_weighted]
    price_spread = (max(prices_only) - min(prices_only)) / (sum(prices_only) / len(prices_only)) if prices_only else 1.0
    swap_confidence = min(1.0, len(prices_weighted) / 50)
    spread_penalty = min(1.0, price_spread / 50)
    confidence = max(0.0, min(1.0, swap_confidence * (1 - spread_penalty)))

    time_span = (recent_swaps[0]["timestamp"] - recent_swaps[-1]["timestamp"]) / 60 if len(recent_swaps) > 1 else 0

    return {
        "price_usd": round(vwap, 18),
        "confidence": round(confidence, 4),
        "swap_count": len(prices_weighted),
        "time_span_minutes": round(time_span, 1),
        "source": "own_dex_events",
    }
