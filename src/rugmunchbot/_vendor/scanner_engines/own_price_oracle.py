# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_price_oracle.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Price Oracle — ClickHouse-Powered CoinGecko Replacement
=============================================================
Phase 1 of the Data Ownership Strategy.

Eliminates 47 CoinGecko API calls per token scan by computing
TWAP (Time-Weighted Average Price) from chain-indexer dex_events.

Architecture:
  - Computes volume-weighted average price (VWAP) from DEX swap events
  - Returns CoinGecko-compatible response structure
  - Three-tier fallback: OWN → DeFiLlama → CoinGecko
  - Caches results per token with configurable TTL

Usage:
    from rugmunchbot._vendor.scanner_engines.own_price_oracle import get_token_price

    result = await get_token_price("0x...", "ethereum")
    # result = {
    #     "price_usd": 0.001234,
    #     "price_change_24h_pct": 5.2,
    #     "volume_24h_usd": 12345.67,
    #     "confidence": 0.85,
    #     "sources": ["own_dex_events"],
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.dex_events (47M+ DEX swaps)
  Fallback 1: DeFiLlama free API (api.llama.fi)
  Fallback 2: CoinGecko free API (api.coingecko.com)
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Price cache ──────────────────────────────────────────────────────────
_PRICE_CACHE: dict[str, dict[str, Any]] = {}
_PRICE_CACHE_TTL = 60  # seconds — prices change faster than DEX data
_PRICE_CACHE_MAX = 200

# ── Chain normalization ─────────────────────────────────────────────────
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

# CoinGecko platform IDs for fallback
_COINGECKO_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "bsc": "binance-smart-chain",
    "polygon": "polygon-pos",
    "arbitrum": "arbitrum-one",
    "optimism": "optimistic-ethereum",
    "base": "base",
    "avalanche": "avalanche",
    "fantom": "fantom",
}

# DeFiLlama chain prefixes
_DEFILLAMA_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "fantom": "fantom",
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_token_price(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Get token price from our data (primary) with fallback chain.

    Fallback chain:
      1. OWN: ClickHouse dex_events TWAP (preferred — 5-50ms, zero cost)
      2. DeFiLlama: api.llama.fi/prices/current/ (free, no key)
      3. CoinGecko: api.coingecko.com (free, rate-limited, last resort)

    Args:
        token_address: Token contract address
        chain: Chain name (ethereum, bsc, solana, etc.)
        force_fresh: Bypass cache and query fresh data

    Returns:
        Dict with price data matching CoinGecko-compatible schema:
        {
            "price_usd": float,
            "volume_24h_usd": float,
            "price_change_24h_pct": float,
            "market_cap_usd": float or None,
            "confidence": float (0-1),
            "sources": [str],
            "source": str,  # "own", "defillama", "coingecko", or "unavailable"
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    # Check cache
    cache_key = f"{token_address}:{chain}"
    if not force_fresh and cache_key in _PRICE_CACHE:
        cached = _PRICE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _PRICE_CACHE_TTL:
            result = {k: v for k, v in cached.items() if not k.startswith("_")}
            result["source"] = cached.get("source", "own")
            return result

    # Tier 1: OWN data from dex_events
    price_data = await _get_own_price(token_address, chain)
    if price_data and price_data.get("confidence", 0) > 0.3:
        price_data["source"] = "own"
        _cache_price(cache_key, price_data)
        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "own_price_oracle_latency",
            ms=round(_elapsed_ms, 1),
            price_usd=price_data.get("price_usd"),
            chain=chain,
            confidence=price_data.get("confidence"),
        )
        return price_data

    # Tier 2: DeFiLlama fallback
    price_data = await _get_defillama_price(token_address, chain)
    if price_data:
        price_data["source"] = "defillama"
        _cache_price(cache_key, price_data)
        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "own_price_oracle_latency",
            ms=round(_elapsed_ms, 1),
            price_usd=price_data.get("price_usd"),
            chain=chain,
            confidence=price_data.get("confidence"),
        )
        return price_data

    # Tier 3: CoinGecko fallback
    price_data = await _get_coingecko_price(token_address, chain)
    if price_data:
        price_data["source"] = "coingecko"
        _cache_price(cache_key, price_data)
        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "own_price_oracle_latency",
            ms=round(_elapsed_ms, 1),
            price_usd=price_data.get("price_usd"),
            chain=chain,
            confidence=price_data.get("confidence"),
        )
        return price_data

    # All sources exhausted
    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_price_oracle_latency",
        ms=round(_elapsed_ms, 1),
        price_usd=0.0,
        chain=chain,
        confidence=0.0,
    )
    return {
        "price_usd": 0.0,
        "volume_24h_usd": 0.0,
        "price_change_24h_pct": 0.0,
        "market_cap_usd": None,
        "confidence": 0.0,
        "sources": [],
        "source": "unavailable",
    }


async def get_batch_prices(
    tokens: list[tuple[str, str]],
) -> dict[str, dict[str, Any]]:
    """Get prices for multiple tokens in batch.

    Args:
        tokens: List of (token_address, chain) tuples

    Returns:
        Dict mapping "token_address:chain" to price data
    """
    results: dict[str, dict[str, Any]] = {}
    for token_address, chain in tokens:
        key = f"{token_address.lower()}:{_normalize_chain(chain)}"
        results[key] = await get_token_price(token_address, chain)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# TIER 1: OWN PRICE FROM CLICKHOUSE DEX_EVENTS
# ═══════════════════════════════════════════════════════════════════════════


async def _get_own_price(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Compute TWAP/VWAP from dex_events.

    Queries swaps from the last hour and computes a volume-weighted
    average price. Also computes 24h metrics for compatibility.

    Returns None if insufficient swap data.
    """
    from rugmunchbot._vendor.scanner_engines.own_dex_data import _query_dex_swaps, get_token_price_simple

    # Get VWAP from recent swaps (1 hour window for fresh price)
    vwap_result = await get_token_price_simple(
        token_address=token_address,
        chain=chain,
        lookback_minutes=60,
    )

    if vwap_result["source"] in ("insufficient_data", "no_valid_price"):
        return None

    price_usd = vwap_result["price_usd"]
    if price_usd <= 0:
        return None

    # Get 24h swap data for volume and price change
    swaps_24h = await _query_dex_swaps(token_address, chain, hours_back=24)
    if not swaps_24h:
        return await _get_own_price_slim(token_address, chain, vwap_result)

    # Compute 24h volume (sum of our token amounts)
    is_token0_cache: dict[str, bool] = {}
    volume_24h = 0.0
    first_price = None
    last_price = None

    for swap in swaps_24h:
        pool = swap["contract_address"]
        if pool not in is_token0_cache:
            is_token0_cache[pool] = swap["token0"].lower() == token_address
        is_token0 = is_token0_cache[pool]

        if is_token0:
            vol = float(swap["amount0_in"] or 0) + float(swap["amount0_out"] or 0)
            paired_in = float(swap["amount1_in"] or 0)
            paired_out = float(swap["amount1_out"] or 0)
        else:
            vol = float(swap["amount1_in"] or 0) + float(swap["amount1_out"] or 0)
            paired_in = float(swap["amount0_in"] or 0)
            paired_out = float(swap["amount0_out"] or 0)

        volume_24h += vol

        # Track first/last for price change
        if vol > 0 and (paired_in > 0 or paired_out > 0):
            p = paired_in / vol if is_token0 else paired_out / vol
            if 0 < p < 1_000_000:
                if first_price is None:
                    first_price = p
                last_price = p

    # Price change over 24h
    price_change_24h = 0.0
    if first_price and last_price and first_price > 0:
        price_change_24h = ((last_price - first_price) / first_price) * 100

    return {
        "price_usd": round(price_usd, 18),
        "volume_24h_usd": round(volume_24h * price_usd, 2),
        "price_change_24h_pct": round(price_change_24h, 2),
        "market_cap_usd": None,  # Cannot compute without circulating supply
        "confidence": vwap_result["confidence"],
        "sources": ["own_dex_events"],
    }


async def _get_own_price_slim(
    token_address: str,
    chain: str,
    vwap_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Return price with just VWAP data when no 24h history exists for new tokens."""
    price_usd = vwap_result["price_usd"]
    if price_usd <= 0:
        return None

    return {
        "price_usd": round(price_usd, 18),
        "volume_24h_usd": 0.0,
        "price_change_24h_pct": 0.0,
        "market_cap_usd": None,
        "confidence": vwap_result["confidence"],
        "sources": ["own_dex_events"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# TIER 2: DEFILLAMA FALLBACK
# ═══════════════════════════════════════════════════════════════════════════


async def _get_defillama_price(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Get token price from DeFiLlama free API."""
    prefix = _DEFILLAMA_CHAIN_MAP.get(chain)
    if not prefix:
        return None

    llama_id = f"{prefix}:{token_address}"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://api.llama.fi/prices/current/{llama_id}")
            if resp.status_code != 200:
                return None

            data = resp.json()
            coins = data.get("coins", {})
            coin_data = coins.get(llama_id) or coins.get(token_address)
            if not coin_data:
                return None

            price = float(coin_data.get("price", 0) or 0)
            if price <= 0:
                return None

            return {
                "price_usd": price,
                "volume_24h_usd": float(coin_data.get("volume24h", 0) or 0),
                "price_change_24h_pct": 0.0,  # DeFiLlama doesn't provide this for all
                "market_cap_usd": float(coin_data.get("marketCap", 0)) if coin_data.get("marketCap") else None,
                "confidence": float(coin_data.get("confidence", 0.5) or 0.5),
                "sources": ["defillama"],
            }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError) as exc:
        logger.debug("DeFiLlama price failed for %s: %s", token_address, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# TIER 3: COINGECKO FALLBACK (LAST RESORT)
# ═══════════════════════════════════════════════════════════════════════════


async def _get_coingecko_price(
    token_address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Get token price from CoinGecko free API (rate-limited, last resort)."""
    platform = _COINGECKO_CHAIN_MAP.get(chain)
    if not platform:
        return None

    try:
        import httpx

        url = f"https://api.coingecko.com/api/v3/coins/{platform}/contract/{token_address}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None

            cg_data = resp.json()
            md = cg_data.get("market_data", {})
            price = float(md.get("current_price", {}).get("usd", 0) or 0)
            if price <= 0:
                return None

            return {
                "price_usd": price,
                "volume_24h_usd": float(md.get("total_volume", {}).get("usd", 0) or 0),
                "price_change_24h_pct": float(md.get("price_change_percentage_24h", 0) or 0),
                "market_cap_usd": float(md.get("market_cap", {}).get("usd", 0))
                if md.get("market_cap", {}).get("usd")
                else None,
                "confidence": 0.9,
                "sources": ["coingecko"],
            }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError) as exc:
        logger.debug("CoinGecko price failed for %s: %s", token_address, exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_price(key: str, data: dict[str, Any]) -> None:
    """Cache price data with timestamp."""
    data["_cached_at"] = time.time()
    _PRICE_CACHE[key] = data
    if len(_PRICE_CACHE) > _PRICE_CACHE_MAX:
        _PRICE_CACHE.pop(next(iter(_PRICE_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    now = time.time()
    return {
        "cache_size": len(_PRICE_CACHE),
        "cache_max": _PRICE_CACHE_MAX,
        "cache_ttl_seconds": _PRICE_CACHE_TTL,
        "entries": {
            k: {
                "age_seconds": round(now - v.get("_cached_at", 0), 1),
                "source": v.get("source", "unknown"),
            }
            for k, v in list(_PRICE_CACHE.items())[:20]
        },
    }


def clear_cache() -> None:
    """Clear the in-process price cache (for testing)."""
    _PRICE_CACHE.clear()
