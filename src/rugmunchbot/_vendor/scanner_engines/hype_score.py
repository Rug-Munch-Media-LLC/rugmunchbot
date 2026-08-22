# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/hype_score.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
C5 — Hype Score (v1) — Composite Token Momentum Score
======================================================
Computes a 0-100 "is this thing heating up" score from free social signals
and our own on-chain velocity. No paid sentiment APIs (frugality principle).

Components (spec C5):
  1. Social velocity  (40%) — CoinGecko trending + DexScreener pair activity
     via ``app.unified_scanner.trending.get_trending_tokens`` (already merges
     CoinGecko + DexScreener + GeckoTerminal).
  2. Volume growth    (30%) — own ``dex_events`` rolling 15m vs trailing 4h
     (per-hour baseline).
  3. Holder growth    (20%) — own ``evm_transfers`` unique receivers/day
     (today vs previous day).
  4. Price momentum   (10%) — own F2 price oracle 24h price change.

The composite decays exponentially with token age: factor 0.5 per 72h since
the last observed activity (a token that has been quiet for 3 days shows half
the hype it would have shown at peak activity). Every signal is normalized to
0-100 and the breakdown is returned so the number is explainable.

Data sources (all LOCAL / free):
  - ``unified_scanner/trending.py`` — free external trending aggregation
  - ``wallet_memory.dex_events``    — own volume velocity
  - ``wallet_memory.evm_transfers`` — own holder growth
  - F2 price oracle                 — own price momentum

Caching:
  - Redis key ``hype_score:{chain}:{token}`` with 15m TTL (graceful fallback).
  - In-process dict mirror (5 min).

Usage:
    from rugmunchbot._vendor.scanner_engines.hype_score import compute_hype_score

    score = await compute_hype_score("0x...", "ethereum")
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import httpx

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

# Legacy module-local names kept so existing unit tests that patch
# ``hype_score._redis_get`` / ``hype_score._redis_set`` keep intercepting the
# shared implementation.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_TTL_SECONDS = 900  # 15 minutes
REDIS_CACHE_PREFIX = "hype_score"
_HYPE_CACHE: dict[str, dict[str, Any]] = {}
_HYPE_CACHE_TTL = 300  # 5 minutes in-process mirror
_HYPE_CACHE_MAX = 300

# ── Time-boxing ────────────────────────────────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10
_TRENDING_TIMEOUT = 10.0
_PRICE_TIMEOUT = 6.0

# ── Component weights (spec C5) ────────────────────────────────────────────
W_SOCIAL = 0.40
W_VOLUME = 0.30
W_HOLDER = 0.20
W_PRICE = 0.10

# Exponential decay: score halves every 72h of inactivity
_DECAY_HALF_LIFE_HOURS = 72.0


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def compute_hype_score(
    token_address: str,
    chain: str = "ethereum",
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Compute the composite hype score for a token.

    Args:
        token_address: Token contract address (lowercased internally).
        chain: Chain name (ethereum, bsc, base, ...).
        force_fresh: Bypass all caches.

    Returns:
        Dict with the composite score and per-signal breakdown::

            {
                "check": "hype_score",
                "result": "scanned" | "unavailable" | "error",
                "token_address": "0x...",
                "chain": "ethereum",
                "hype_score": 0-100,
                "decay_factor": 0-1,
                "inactivity_hours": float,
                "signals": {
                    "social_velocity": {"score", "trending_hit", "detail"},
                    "volume_growth": {"score", "ratio", "detail"},
                    "holder_growth": {"score", "ratio", "detail"},
                    "price_momentum": {"score", "change_24h_pct", "detail"},
                },
                "weights": {"social": 0.4, "volume": 0.3, "holder": 0.2, "price": 0.1},
            }
    """
    token = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token or not chain_norm:
        return _error_result(token, chain_norm, "token_address and chain are required")

    # ── 1. Redis fast cache ──
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm}:{token}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("hype_score_redis_hit", key=cache_key)
            return cached

    # ── 2. In-process mirror cache ──
    if not force_fresh and cache_key in _HYPE_CACHE:
        entry = _HYPE_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _HYPE_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full computation ──
    try:
        result = await _compute_hype(token, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("hype_score_query_failed: %s", exc)
        result = _unavailable_result(token, chain_norm)

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _HYPE_CACHE[cache_key] = result
        if len(_HYPE_CACHE) > _HYPE_CACHE_MAX:
            _HYPE_CACHE.pop(next(iter(_HYPE_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "hype_score_latency",
        ms=round(_elapsed_ms, 1),
        token=token[:16],
        chain=chain_norm,
        score=result.get("hype_score"),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_hype(token: str, chain: str) -> dict[str, Any]:
    """Run all component signals and combine them into the composite."""
    # ── Round 1: independent signals (parallel) ──
    social_task = asyncio.create_task(_social_velocity(token, chain))
    volume_task = asyncio.create_task(_volume_growth(token, chain))
    holder_task = asyncio.create_task(_holder_growth(token, chain))
    price_task = asyncio.create_task(_price_momentum(token, chain))
    social: Any
    volume: Any
    holder: Any
    price: Any
    social, volume, holder, price = await asyncio.gather(
        social_task, volume_task, holder_task, price_task, return_exceptions=True
    )

    social = _as_signal(social)
    volume = _as_signal(volume)
    holder = _as_signal(holder)
    price = _as_signal(price)

    # ── Composite (weighted) ──
    composite = (
        W_SOCIAL * social["score"] + W_VOLUME * volume["score"] + W_HOLDER * holder["score"] + W_PRICE * price["score"]
    )

    # ── Exponential decay by inactivity (factor 0.5 per 72h) ──
    inactivity_hours = await _inactivity_hours(token, chain)
    decay = 0.5 ** (inactivity_hours / _DECAY_HALF_LIFE_HOURS) if inactivity_hours is not None else 1.0
    decay = max(0.05, min(1.0, decay))  # floor at 5% so score never fully dies

    hype_score = round(composite * decay, 1)

    return {
        "check": "hype_score",
        "result": "scanned",
        "source": "own",
        "token_address": token,
        "chain": chain,
        "hype_score": hype_score,
        "decay_factor": round(decay, 4),
        "inactivity_hours": inactivity_hours,
        "signals": {
            "social_velocity": social,
            "volume_growth": volume,
            "holder_growth": holder,
            "price_momentum": price,
        },
        "weights": {"social": W_SOCIAL, "volume": W_VOLUME, "holder": W_HOLDER, "price": W_PRICE},
    }


def _as_signal(value: Any) -> dict[str, Any]:
    """Coerce a signal result into a normalized dict (never raises)."""
    if isinstance(value, BaseException):
        return {"score": 0.0, "detail": f"signal_error: {type(value).__name__}"}
    if isinstance(value, dict):
        value.setdefault("score", 0.0)
        return value
    return {"score": 0.0, "detail": "unknown_signal_shape"}


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 1 — SOCIAL VELOCITY (40%)
# ═══════════════════════════════════════════════════════════════════════════


async def _social_velocity(token: str, chain: str) -> dict[str, Any]:
    """Score social velocity from the trending merger (CoinGecko + DexScreener)."""
    try:
        from app.unified_scanner.trending import get_trending_tokens

        trending = await asyncio.wait_for(get_trending_tokens(chain=chain, limit=50), timeout=_TRENDING_TIMEOUT)
        tokens = trending.get("tokens", []) if isinstance(trending, dict) else []
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError, RuntimeError) as exc:
        logger.debug("hype_social_trending_failed: %s", exc)
        return {"score": 0.0, "trending_hit": False, "detail": "trending_feed_unavailable"}

    if not tokens:
        return {"score": 0.0, "trending_hit": False, "detail": "no_trending_data"}

    token_l = token.lower()
    hits = [t for t in tokens if str(t.get("address", "")).lower() == token_l]
    if not hits:
        return {"score": 0.0, "trending_hit": False, "detail": "not_in_trending_feed"}

    # Multiple sources may list the token — take the strongest signal
    best = max(hits, key=lambda t: float(t.get("trending_score", 0) or 0))
    ts = float(best.get("trending_score", 0) or 0)
    vol24 = float(best.get("volume_24h", 0) or 0)
    price_change = float(best.get("price_change_24h", 0) or 0)

    # Trending presence (0-60) + pair activity volume (0-25) + change (0-15)
    presence = min(60.0, 20.0 + ts * 4.0) if ts > 0 else 30.0
    volume_component = min(25.0, (vol24 / 50_000.0) * 25.0)
    change_component = max(0.0, min(15.0, 7.5 + price_change * 0.075))
    score = round(min(100.0, presence + volume_component + change_component), 1)

    return {
        "score": score,
        "trending_hit": True,
        "trending_score": ts,
        "volume_24h": vol24,
        "price_change_24h": price_change,
        "detail": f"trending_rank={ts:.1f} vol24=${vol24:,.0f}",
    }


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 2 — VOLUME GROWTH (30%)
# ═══════════════════════════════════════════════════════════════════════════


async def _volume_growth(token: str, chain: str) -> dict[str, Any]:
    """Score volume growth: rolling 15m USD vs trailing-4h hourly baseline."""
    try:
        short_raw, long_raw = await run_sync(_query_volume_raw, token, chain)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("hype_volume_query_failed: %s", exc)
        return {"score": 0.0, "detail": "volume_query_failed"}

    if short_raw is None or long_raw is None:
        return {"score": 0.0, "detail": "no_own_volume_data"}

    # Convert raw amounts to USD once per token (async layer — the F2 oracle
    # is async and must never be called inside a sync thread).
    short_usd = await _raw_to_usd(token, chain, short_raw)
    long_usd = await _raw_to_usd(token, chain, long_raw)
    if short_usd is None or long_usd is None:
        return {"score": 0.0, "detail": "price_unavailable_for_volume"}

    baseline_per_15m = long_usd / 16.0 if long_usd > 0 else 0.0
    # No trailing baseline → require meaningful 15m activity to score
    ratio = 0.0 if baseline_per_15m <= 0 else short_usd / baseline_per_15m

    # ratio >= 4 → full score; linear ramp below
    score = round(min(100.0, ratio / 4.0 * 100.0), 1)

    return {
        "score": score,
        "ratio": round(ratio, 2),
        "volume_15m_usd": round(short_usd, 2),
        "volume_4h_usd": round(long_usd, 2),
        "detail": f"15m=${short_usd:,.0f} 4h=${long_usd:,.0f} ratio={ratio:.2f}x",
    }


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 3 — HOLDER GROWTH (20%)
# ═══════════════════════════════════════════════════════════════════════════


async def _holder_growth(token: str, chain: str) -> dict[str, Any]:
    """Score holder growth: unique receivers today vs previous day."""
    try:
        today, prev = await run_sync(_query_holder_growth, token, chain)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("hype_holder_query_failed: %s", exc)
        return {"score": 0.0, "detail": "holder_query_failed"}

    if today is None or prev is None:
        return {"score": 0.0, "detail": "no_holder_data"}

    # ratio: growth multiplier over previous-day baseline
    ratio = (float(today) if today > 0 else 0.0) if prev <= 0 else today / prev

    # ratio >= 2 → full score; linear ramp
    score = round(min(100.0, ratio / 2.0 * 100.0), 1)

    return {
        "score": score,
        "ratio": round(ratio, 2),
        "holders_today": int(today),
        "holders_prev_day": int(prev),
        "detail": f"today={today} prev={prev} ratio={ratio:.2f}x",
    }


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 4 — PRICE MOMENTUM (10%)
# ═══════════════════════════════════════════════════════════════════════════


async def _price_momentum(token: str, chain: str) -> dict[str, Any]:
    """Score price momentum from the F2 price oracle 24h change."""
    try:
        from rugmunchbot._vendor.scanner_engines.price_oracle import get_token_price

        price = await asyncio.wait_for(get_token_price(token, chain), timeout=_PRICE_TIMEOUT)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError, RuntimeError) as exc:
        logger.debug("hype_price_oracle_failed: %s", exc)
        return {"score": 0.0, "detail": "price_oracle_unavailable"}

    if not price:
        return {"score": 0.0, "detail": "no_price"}

    change = float(price.get("price_change_24h_pct", 0) or 0)
    # change +100% → 100, 0% → 50, -100% → 0
    score = round(max(0.0, min(100.0, 50.0 + change * 0.5)), 1)

    return {
        "score": score,
        "change_24h_pct": change,
        "price_usd": float(price.get("price_usd", 0) or 0),
        "price_source": price.get("source", ""),
        "detail": f"24h_change={change:+.1f}%",
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_volume_raw(token: str, chain: str) -> tuple[float | None, float | None]:
    """Return (15m raw amount, 4h raw amount) for the token side of swaps.

    Uses the token side of the swap (amount0 when the token is token0,
    amount1 when it is token1). USD conversion happens at the async layer.
    """
    client = _get_ch_client()
    if client is None:
        return None, None

    params: dict[str, Any] = {"addr0": token, "addr1": token, "chain": chain}
    try:
        rows = client.execute(
            """
            SELECT
                sumIf(amount0, token0 = %(addr0)s AND block_timestamp >= now() - INTERVAL 15 MINUTE)
                + sumIf(amount1, token1 = %(addr1)s AND block_timestamp >= now() - INTERVAL 15 MINUTE) AS short_raw,
                sumIf(amount0, token0 = %(addr0)s AND block_timestamp >= now() - INTERVAL 4 HOUR)
                + sumIf(amount1, token1 = %(addr1)s AND block_timestamp >= now() - INTERVAL 4 HOUR) AS long_raw
            FROM wallet_memory.dex_events
            WHERE chain = %(chain)s
              AND (token0 = %(addr0)s OR token1 = %(addr1)s)
              AND block_timestamp >= now() - INTERVAL 4 HOUR
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("hype_volume_query_failed: %s", exc)
        return None, None

    if not rows or not rows[0]:
        return None, None

    return float(rows[0][0] or 0), float(rows[0][1] or 0)


def _query_holder_growth(token: str, chain: str) -> tuple[int | None, int | None]:
    """Return (unique receivers today, unique receivers previous day)."""
    client = _get_ch_client()
    if client is None:
        return None, None

    params: dict[str, Any] = {"addr": token, "chain": chain}
    try:
        rows = client.execute(
            """
            SELECT
                uniqIf(to_address, block_timestamp >= now() - INTERVAL 1 DAY) AS today,
                uniqIf(to_address, block_timestamp >= now() - INTERVAL 2 DAY
                                       AND block_timestamp < now() - INTERVAL 1 DAY) AS prev
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(addr)s
              AND chain = %(chain)s
              AND to_address != '0x0000000000000000000000000000000000000000'
              AND block_timestamp >= now() - INTERVAL 2 DAY
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("hype_holder_query_failed: %s", exc)
        return None, None

    if not rows or not rows[0]:
        return None, None
    return int(rows[0][0] or 0), int(rows[0][1] or 0)


def _query_last_activity(token: str, chain: str) -> Any:
    """Return the most recent block_timestamp for a token (dex or transfers)."""
    client = _get_ch_client()
    if client is None:
        return None

    params: dict[str, Any] = {"addr0": token, "addr1": token, "addr": token, "chain": chain}
    try:
        rows = client.execute(
            """
            SELECT max(ts) FROM (
                SELECT max(block_timestamp) AS ts
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s AND (token0 = %(addr0)s OR token1 = %(addr1)s)
                UNION ALL
                SELECT max(block_timestamp) AS ts
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND token_address = %(addr)s
            )
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("hype_last_activity_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None
    return rows[0][0]


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _inactivity_hours(token: str, chain: str) -> float | None:
    """Hours since the token's last observed activity (None when unknown)."""
    last_ts = await run_sync(_query_last_activity, token, chain)
    if last_ts is None:
        return None
    try:
        if hasattr(last_ts, "replace"):
            last_naive = last_ts.replace(tzinfo=UTC) if last_ts.tzinfo is None else last_ts
        else:
            last_naive = datetime.fromisoformat(str(last_ts))
            if last_naive.tzinfo is None:
                last_naive = last_naive.replace(tzinfo=UTC)
        now_utc = datetime.now(UTC)
        delta = now_utc - last_naive
        return max(0.0, delta.total_seconds() / 3600.0)
    except (ValueError, TypeError, AttributeError) as exc:
        logger.debug("hype_inactivity_calc_failed: %s", exc)
        return None


async def _raw_to_usd(token: str, chain: str, raw_amount: float) -> float | None:
    """Convert a raw amount to USD via the F2 price oracle (cached per token).

    Async — must be called from the event loop, never inside a sync thread.
    """
    if raw_amount <= 0:
        return 0.0
    try:
        from rugmunchbot._vendor.scanner_engines.price_oracle import (
            get_token_decimals,
            get_token_price,
        )

        price_result = _PRICE_CACHE.get((token, chain))
        if price_result is None:
            price_result = await get_token_price(token, chain)
            _PRICE_CACHE[(token, chain)] = price_result
        if not price_result:
            return None
        price_usd = float(price_result.get("price_usd", 0) or 0)
        if price_usd <= 0:
            return None
        decimals = _DECIMALS_CACHE.get((token, chain))
        if decimals is None:
            decimals = await get_token_decimals(token, chain)
            _DECIMALS_CACHE[(token, chain)] = decimals
        if decimals is None:
            decimals = 18
        human = raw_amount / (10**decimals)
        return human * price_usd
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError, RuntimeError) as exc:
        logger.debug("hype_raw_to_usd_failed %s: %s", token[:16], exc)
        return None


# Per-process price/decimals caches for USD conversion (bounded)
_PRICE_CACHE: dict[tuple[str, str], dict[str, Any] | None] = {}
_DECIMALS_CACHE: dict[tuple[str, str], int | None] = {}


def _unavailable_result(token: str, chain_norm: str) -> dict[str, Any]:
    """Graceful degradation result."""
    return {
        "check": "hype_score",
        "result": "unavailable",
        "source": "own",
        "token_address": token,
        "chain": chain_norm,
        "hype_score": 0,
        "decay_factor": 1.0,
        "inactivity_hours": None,
        "signals": {
            "social_velocity": {"score": 0.0, "detail": "unavailable"},
            "volume_growth": {"score": 0.0, "detail": "unavailable"},
            "holder_growth": {"score": 0.0, "detail": "unavailable"},
            "price_momentum": {"score": 0.0, "detail": "unavailable"},
        },
        "weights": {"social": W_SOCIAL, "volume": W_VOLUME, "holder": W_HOLDER, "price": W_PRICE},
    }


def _error_result(token: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result."""
    return {
        "check": "hype_score",
        "result": "error",
        "error": message,
        "token_address": token,
        "chain": chain_norm,
        "hype_score": 0,
        "decay_factor": 1.0,
        "inactivity_hours": None,
        "signals": {},
        "weights": {"social": W_SOCIAL, "volume": W_VOLUME, "holder": W_HOLDER, "price": W_PRICE},
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _HYPE_CACHE,
        _HYPE_CACHE_MAX,
        _HYPE_CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "price_cache_size": len(_PRICE_CACHE),
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear in-process caches (for testing)."""
    _common_clear_cache(_HYPE_CACHE, _PRICE_CACHE, _DECIMALS_CACHE)
