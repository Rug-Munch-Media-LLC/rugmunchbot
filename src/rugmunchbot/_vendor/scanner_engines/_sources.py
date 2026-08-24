# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_sources.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Price source layer — own-dex-events fetch, ClickHouse/Redis storage, caches.

Extracted from ``engines/price_oracle.py`` (degodding wave, 2026-08-14) so the
source-level fetch logic lives beside the storage that backs it instead of
sharing a module with the oracle's tier aggregation.

This module owns:

- The lazy ClickHouse client (``wallet_memory`` database) + ``token_meta`` /
  ``token_prices`` read/write helpers (``_get_ch_client``,
  ``ensure_token_meta_table``, ``_meta_upsert``, ``_meta_get``).
- The lazy async Redis client + JSON cache helpers (``_get_redis``,
  ``_redis_get``, ``_redis_set``).
- The in-process caches (``_price_mem``, ``_decimals_mem``, ``_native_mem``)
  and the write-through cache layers (``_get_cached_price``,
  ``_cache_set_price``).
- The OWN-DATA price source — dex_events reserve ratios + VWAP fallback
  (``_own_dex_events``, ``_own_price_argmax``, ``_own_price_vwap``,
  ``_anchor_to_usd``) and the historical swap queries
  (``_query_swaps_between``, ``_vwap_price_from_swaps``).
- The public monitoring helpers ``get_cache_stats`` / ``clear_cache`` (they
  introspect the cache state that lives here).

Imports are intentionally one-directional: the oracle (``_oracle.py`` /
``price_oracle.py``) imports these at module load; this module never imports
the oracle at module load. The few source functions that need an oracle
function (``get_token_decimals``, ``get_native_price``, ``get_token_price``)
lazy-import them from ``price_oracle`` at call time, matching the established
lazy import pattern used by ``_price_adapters.py``.

Every function is defensive: type-hinted, docstringed, wrapped in try/except,
and fails gracefully (return ``None`` / ``0.0``) when ClickHouse, Redis, or
the free APIs are unreachable.
"""

from __future__ import annotations

import math
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.exceptions import RedisError

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
from rugmunchbot._vendor.scanner_engines._price_constants import (
    _MEM_MAX,
    _MEM_TTL,
    _PRICE_TTL,
    _STABLECOINS,
    _WRAPPED_NATIVE,
)
from rugmunchbot._vendor.scanner_engines._price_helpers import (
    _collect_weighted_prices,
    _normalize_token,
)

logger = get_logger(__name__)

# ── In-process caches ─────────────────────────────────────────────────────
_price_mem: dict[str, dict[str, Any]] = {}
_decimals_mem: dict[str, int] = {}
_native_mem: dict[str, dict[str, Any]] = {}

# ── Lazy clients ──────────────────────────────────────────────────────────
_ch_client: Any = None
_redis_client: Any = None


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE CLIENT + token_meta TABLE
# ═══════════════════════════════════════════════════════════════════════════


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for the wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        _ch_client.execute("SELECT 1")
        logger.info("price_oracle ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError, CHDriverError) as exc:
        logger.warning("price_oracle ClickHouse unavailable: %s", exc)
        _ch_client = None
        return None


async def ensure_token_meta_table() -> bool:
    """Ensure the ClickHouse token_prices table exists.

    ``wallet_memory.token_meta`` is now a VIEW over ``token_meta_ref`` +
    ``token_prices`` (see the lite-chain-indexer migration), so the price
    oracle persists into the underlying ``token_prices`` ReplacingMergeTree
    table instead. This function creates it idempotently.

    Returns True when the table is guaranteed present (or already was),
    False when ClickHouse is unreachable.
    """

    def _ensure() -> bool:
        client = _get_ch_client()
        if client is None:
            return False
        try:
            client.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_memory.token_prices (
                    chain LowCardinality(String),
                    token_address String,
                    symbol String DEFAULT '',
                    price_usd Float64,
                    updated_at DateTime DEFAULT now()
                ) ENGINE = ReplacingMergeTree()
                ORDER BY (chain, token_address)
                """
            )
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_prices ensure failed: %s", exc)
            return False

    return bool(await run_sync(_ensure))


async def _meta_upsert(
    token_address: str,
    chain: str,
    *,
    symbol: str = "",
    price_usd: float | None = None,
) -> None:
    """Upsert a token_prices row (ReplacingMergeTree replaces on merge).

    Writes to ``wallet_memory.token_prices`` — ``token_meta`` is a VIEW
    over token_meta_ref + token_prices and cannot accept INSERTs.
    Name/decimals live in token_meta_ref; only price + symbol go here.
    """

    def _write() -> None:
        client = _get_ch_client()
        if client is None:
            return
        try:
            client.execute(
                """
                INSERT INTO wallet_memory.token_prices
                    (chain, token_address, symbol, price_usd, updated_at)
                VALUES
                """,
                [(chain, token_address, symbol, price_usd, datetime.now(UTC))],
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_prices upsert failed for %s: %s", token_address[:16], exc)

    await run_sync(_write)


async def _meta_get(token_address: str, chain: str) -> dict[str, Any] | None:
    """Read the latest token_meta row (by updated_at) for a token."""

    def _read() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return list(
                client.execute(
                    """
                    SELECT token_address, chain, symbol, name, decimals,
                           last_price_usd, last_price_source, updated_at
                    FROM wallet_memory.token_meta
                    WHERE token_address = %(token)s AND chain = %(chain)s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    {"token": token_address, "chain": chain},
                )
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_meta read failed for %s: %s", token_address[:16], exc)
            return []

    rows = await run_sync(_read)
    if not rows:
        return None
    row = rows[0]
    return {
        "token_address": row[0],
        "chain": row[1],
        "symbol": row[2],
        "name": row[3],
        "decimals": row[4],
        "last_price_usd": row[5],
        "last_price_source": row[6],
        "updated_at": row[7].timestamp() if hasattr(row[7], "timestamp") else time.time(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# REDIS CACHE HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _get_redis() -> Any | None:
    """Lazy async Redis client (graceful when Redis is unreachable)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        host = os.getenv("REDIS_HOST", "rmi-redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD") or None
        _redis_client = aioredis.Redis(
            host=host,
            port=port,
            password=password,
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=3,
        )
        return _redis_client
    except (ImportError, OSError, ValueError) as exc:
        logger.debug("price_oracle Redis init failed: %s", exc)
        _redis_client = None
        return None


async def _redis_get(key: str) -> dict[str, Any] | None:
    """Read a JSON dict from Redis (None on miss / failure)."""
    try:
        import json

        r = await _get_redis()
        if r is None:
            return None
        raw = await r.get(key)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("price_oracle Redis get failed: %s", exc)
        return None


async def _redis_set(key: str, value: dict[str, Any], ttl: int = _PRICE_TTL) -> None:
    """Write a JSON dict to Redis with TTL (best effort)."""
    try:
        import json

        r = await _get_redis()
        if r is None:
            return
        await r.set(key, json.dumps(value), ex=ttl)
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("price_oracle Redis set failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# CACHE LAYERS (in-process -> Redis -> ClickHouse token_meta)
# ═══════════════════════════════════════════════════════════════════════════


async def _get_cached_price(token: str, chain: str, depth: int) -> dict[str, Any] | None:
    """Return a cached price from in-process, Redis, or token_meta (fresh only)."""
    cache_key = f"{chain}:{token}"

    # in-process
    if cache_key in _price_mem:
        cached = _price_mem[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _MEM_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    # Redis
    try:
        redis_val = await _redis_get(f"price:{chain}:{token}")
        if redis_val and float(redis_val.get("price_usd", 0) or 0) > 0:
            redis_val["source"] = f"redis_cache:{redis_val.get('source', 'unknown')}"
            _price_mem[cache_key] = {**redis_val, "_cached_at": time.time()}
            return redis_val
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("price_oracle Redis cache read failed: %s", exc)

    # ClickHouse token_meta (only when fresh within TTL)
    if depth == 0:
        try:
            meta = await _meta_get(token, chain)
            if (
                meta
                and meta.get("last_price_usd") is not None
                and float(meta.get("last_price_usd") or 0) > 0
                and time.time() - float(meta.get("updated_at") or 0) < _PRICE_TTL
            ):
                payload = {
                    "price_usd": float(meta["last_price_usd"]),
                    "source": f"clickhouse_cache:{meta.get('last_price_source') or 'token_meta'}",
                    "timestamp": float(meta.get("updated_at") or time.time()),
                    "confidence": 0.5,
                }
                if meta.get("decimals") is not None:
                    payload["decimals"] = int(meta["decimals"])
                _price_mem[cache_key] = {**payload, "_cached_at": time.time()}
                return payload
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
            logger.debug("price_oracle token_meta cache read failed: %s", exc)

    return None


async def _cache_set_price(token: str, chain: str, payload: dict[str, Any]) -> None:
    """Write-through: in-process + Redis + ClickHouse token_meta."""
    cache_key = f"{chain}:{token}"
    _price_mem[cache_key] = {**payload, "_cached_at": time.time()}
    if len(_price_mem) > _MEM_MAX:
        _price_mem.pop(next(iter(_price_mem)), None)

    await _redis_set(f"price:{chain}:{token}", payload, ttl=_PRICE_TTL)

    try:
        meta = await _meta_get(token, chain)
        decimals = payload.get("decimals")
        if decimals is None and meta and meta.get("decimals") is not None:
            decimals = int(meta["decimals"])
        if decimals is not None:
            payload["decimals"] = decimals
        await _meta_upsert(
            token,
            chain,
            symbol=str(payload.get("symbol", "") or ""),
            price_usd=float(payload.get("price_usd", 0) or 0),
        )
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("price_oracle token_prices write-through failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# TIER 1: OWN DATA — dex_events reserve ratios
# ═══════════════════════════════════════════════════════════════════════════


async def _own_dex_events(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Derive USD price from dex_events reserve ratios (free, 59M rows).

    Primary path: the spec's argMax query — the latest reserve ratio
    `amount1_out / amount0_in` per block for any indexed pair.

    Fallback path: VWAP across recent swaps via own_dex_data when
    token0/token1 columns are empty on historical rows.

    Raw ratios are decimals-normalized and anchored to USD using the
    paired token: stablecoin (~$1), wrapped native (x native USD price),
    or a one-level recursive paired-token price.
    """
    from rugmunchbot._vendor.scanner_engines.price_oracle import get_token_decimals

    raw_price: float | None = None
    paired_token: str = ""
    swap_count = 0

    # Primary: argMax latest ratio (both directions)
    argmax = await _own_price_argmax(token, chain)
    if argmax and argmax.get("price") is not None and argmax.get("price", 0) > 0:
        raw_price = float(argmax["price"])
        paired_token = str(argmax.get("paired_token", "") or "")
        swap_count = int(argmax.get("swap_count", 0) or 0)

    # Fallback: VWAP from recent swaps
    if raw_price is None or not paired_token:
        vwap = await _own_price_vwap(token, chain)
        if vwap and vwap.get("price") is not None and vwap.get("price", 0) > 0:
            raw_price = float(vwap["price"])
            paired_token = str(vwap.get("paired_token", "") or "")
            swap_count = int(vwap.get("swap_count", 0) or 0)

    if raw_price is None or raw_price <= 0:
        return None

    # Decimals-normalize the raw ratio: raw = paired_raw / our_raw
    our_decimals = await get_token_decimals(token, chain)
    paired_decimals = await get_token_decimals(paired_token, chain)
    if our_decimals is None or paired_decimals is None:
        return None
    norm_price = raw_price * math.pow(10, our_decimals) / math.pow(10, paired_decimals)

    # Anchor to USD
    usd_price, confidence, anchor = await _anchor_to_usd(norm_price, paired_token, chain, _depth)

    if usd_price <= 0:
        return None

    source = "own_dex_events" if anchor != "unanchored" else "own_dex_events_unanchored"
    # Confidence grows with swap count (more evidence = more trust)
    evidence = min(1.0, swap_count / 50)
    confidence = max(0.05, min(1.0, confidence * (0.5 + 0.5 * evidence)))

    return {
        "price_usd": usd_price,
        "confidence": round(confidence, 4),
        "swap_count": swap_count,
        "paired_token": paired_token,
        "anchor": anchor,
        "source": source,
    }


async def _own_price_argmax(token: str, chain: str) -> dict[str, Any] | None:
    """Latest reserve ratio from dex_events (the spec's tier-1 SQL)."""

    def _query(side: str) -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        # side == "0": our token is token0, ratio = amount1_out/amount0_in
        # side == "1": our token is token1, ratio = amount0_out/amount1_in
        if side == "0":
            ratio_expr = "amount1_out / amount0_in"
            paired_col = "token1"
            where_extra = "token0 = %(token)s AND amount0_in > 0 AND amount1_out > 0"
        else:
            ratio_expr = "amount0_out / amount1_in"
            paired_col = "token0"
            where_extra = "token1 = %(token)s AND amount1_in > 0 AND amount0_out > 0"
        try:
            # ratio_expr/paired_col/where_extra are internal constants,
            # not user input; params are bound via clickhouse_driver placeholders.
            return list(
                client.execute(
                    f"""
                    SELECT
                        argMax({ratio_expr}, block_number) AS latest_ratio,
                        argMax({paired_col}, block_number) AS paired,
                        count() AS swaps,
                        uniq(contract_address) AS pools
                    FROM wallet_memory.dex_events
                    WHERE chain = %(chain)s
                      AND type = 'swap'
                      AND {where_extra}
                      AND block_timestamp >= now() - INTERVAL 24 HOUR
                    """,  # nosec B608 - internal constant fragments, params bound below
                    {"token": token, "chain": chain},
                )
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("price_oracle argMax query failed (%s): %s", side, exc)
            return []

    for side in ("0", "1"):
        rows = await run_sync(_query, side)
        if not rows or not rows[0]:
            continue
        ratio, paired, swaps, pools = rows[0]
        if ratio is None or float(ratio or 0) <= 0 or not paired:
            continue
        return {
            "price": float(ratio),
            "paired_token": str(paired),
            "swap_count": int(swaps or 0),
            "pools": int(pools or 0),
        }
    return None


async def _own_price_vwap(token: str, chain: str) -> dict[str, Any] | None:
    """VWAP price fallback over recent swaps (handles empty token0/token1)."""
    try:
        from rugmunchbot._vendor.scanner_engines.own_dex_data.client import _load_pair_cache
        from rugmunchbot._vendor.scanner_engines.own_dex_data.query import _query_dex_swaps

        swaps = await _query_dex_swaps(token, chain, hours_back=24)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("price_oracle vwap swap query failed: %s", exc)
        return None
    if len(swaps) < 2:
        return None

    pair_cache = _load_pair_cache(chain)
    prices_weighted, paired_counts = _collect_weighted_prices(swaps, token, pair_cache=pair_cache, strict_match=False)

    if not prices_weighted:
        return None

    total_weight = sum(w for _, w in prices_weighted)
    vwap = sum(p * w for p, w in prices_weighted) / total_weight if total_weight > 0 else 0.0
    paired_token = max(paired_counts, key=lambda k: paired_counts[k]) if paired_counts else ""

    return {
        "price": vwap,
        "paired_token": paired_token,
        "swap_count": len(prices_weighted),
    }


async def _anchor_to_usd(
    norm_price: float,
    paired_token: str,
    chain: str,
    depth: int,
) -> tuple[float, float, str]:
    """Convert a decimals-normalized pair price to USD.

    Returns (usd_price, confidence, anchor_kind):
      - stablecoin pair       -> anchor ~$1.00
      - wrapped native pair   -> x native price
      - other paired token    -> x paired token USD price (one recursion level)
      - unresolvable          -> unanchored value with low confidence
    """
    from rugmunchbot._vendor.scanner_engines.price_oracle import get_native_price, get_token_price

    paired = _normalize_token(paired_token, chain)

    if paired in _STABLECOINS:
        return norm_price, 0.85, "stablecoin"

    if _WRAPPED_NATIVE.get(chain, "").lower() == paired.lower():
        native_price = await get_native_price(chain)
        if native_price > 0:
            return norm_price * native_price, 0.75, "native"
        return 0.0, 0.0, "native_unpriced"

    if depth < 1:
        try:
            paired_price = await get_token_price(paired, chain, _depth=depth + 1)
            if paired_price and float(paired_price.get("price_usd", 0) or 0) > 0:
                return norm_price * float(paired_price["price_usd"]), 0.6, "paired_token"
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("price_oracle paired anchor failed for %s: %s", paired[:16], exc)

    # Last resort: return the normalized ratio flagged as unanchored
    return norm_price, 0.15, "unanchored"


# ═══════════════════════════════════════════════════════════════════════════
# HISTORICAL PRICE HELPERS (own dex_events at a point in time)
# ═══════════════════════════════════════════════════════════════════════════


async def _query_swaps_between(
    token: str,
    chain: str,
    lo_ts: float,
    hi_ts: float,
    *,
    max_swaps: int = 200,
) -> list[dict[str, Any]]:
    """Query dex_events swaps for a token within a historical time range."""

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return list(
                client.execute(
                    """
                    SELECT
                        contract_address, dex, version,
                        token0, token1,
                        amount0_in, amount1_in, amount0_out, amount1_out,
                        block_timestamp, tx_hash
                    FROM wallet_memory.dex_events
                    WHERE chain = %(chain)s
                      AND type = 'swap'
                      AND block_timestamp >= toDateTime(%(lo)s)
                      AND block_timestamp <= toDateTime(%(hi)s)
                      AND (token0 = %(token)s OR token1 = %(token)s)
                    ORDER BY block_timestamp ASC
                    LIMIT %(limit)s
                    """,
                    {
                        "chain": chain,
                        "token": token,
                        "lo": int(lo_ts),
                        "hi": int(hi_ts),
                        "limit": max_swaps,
                    },
                )
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("price_oracle at-time swap query failed for %s: %s", token[:16], exc)
            return []

    rows = await run_sync(_query)
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
                    "amount0_in": float(row[5] or 0),
                    "amount1_in": float(row[6] or 0),
                    "amount0_out": float(row[7] or 0),
                    "amount1_out": float(row[8] or 0),
                    "block_timestamp": row[9],
                    "tx_hash": row[10],
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("price_oracle at-time row parse failed: %s", exc)
            continue
    return swaps


async def _vwap_price_from_swaps(
    token: str,
    chain: str,
    swaps: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compute a USD-anchored VWAP from a list of raw swap dicts.

    Mirrors ``_own_price_vwap`` logic but operates on an explicit swap list
    (already time-bounded by the caller) instead of re-querying ClickHouse.
    """
    from rugmunchbot._vendor.scanner_engines.price_oracle import get_token_decimals

    if not swaps:
        return None

    prices_weighted, paired_counts = _collect_weighted_prices(swaps, token)

    if not prices_weighted:
        return None

    total_weight = sum(w for _, w in prices_weighted)
    vwap = sum(p * w for p, w in prices_weighted) / total_weight if total_weight > 0 else 0.0
    paired_token = max(paired_counts, key=lambda k: paired_counts[k]) if paired_counts else ""
    if vwap <= 0 or not paired_token:
        return None

    # Decimals-normalize: raw = paired_raw / our_raw
    our_decimals = await get_token_decimals(token, chain)
    paired_decimals = await get_token_decimals(paired_token, chain)
    if our_decimals is None or paired_decimals is None:
        return None
    norm_price = vwap * math.pow(10, our_decimals) / math.pow(10, paired_decimals)

    usd_price, confidence, anchor = await _anchor_to_usd(norm_price, paired_token, chain, 0)
    if usd_price <= 0:
        return None

    evidence = min(1.0, len(prices_weighted) / 50)
    confidence = max(0.05, min(1.0, confidence * (0.5 + 0.5 * evidence)))
    return {
        "price_usd": usd_price,
        "confidence": round(confidence, 4),
        "swap_count": len(prices_weighted),
        "paired_token": paired_token,
        "anchor": anchor,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING / TESTING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        extra={
            "price_cache_size": len(_price_mem),
            "price_cache_max": _MEM_MAX,
            "decimals_cache_size": len(_decimals_mem),
            "native_cache_size": len(_native_mem),
            "clickhouse_connected": _ch_client is not None,
            "redis_connected": _redis_client is not None,
        },
    )


def clear_cache() -> None:
    """Clear all in-process caches (for testing / memory pressure)."""
    _common_clear_cache(_price_mem, _decimals_mem, _native_mem)
