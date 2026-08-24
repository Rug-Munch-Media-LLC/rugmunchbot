# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_oracle.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Oracle aggregation layer — tier selection, fallback, USD normalization.

Extracted from ``engines/price_oracle.py`` (degodding wave, 2026-08-14) so the
oracle's decision logic lives apart from the source fetch / storage layer
(``_sources.py``) and the per-provider free-API adapters
(``_price_adapters.py``).

This module owns:

- ``_TIERS`` — the ordered fallback chain (own dex_events -> DexScreener ->
  CoinGecko -> DeFiLlama -> GeckoTerminal -> ParaSwap -> 1inch).
- ``get_token_price`` — the tiered USD price resolution entry point.
- ``get_token_price_at_time`` — historical price resolution near a timestamp.
- ``get_token_decimals`` — decimals resolution (token_meta -> DexScreener ->
  fallback map -> own_dex_data -> chain default).
- ``get_native_price`` — native coin USD price (wrapped token oracle ->
  CoinGecko -> DeFiLlama).
- ``normalize_amount`` — raw on-chain amount -> USD conversion.

Imports are intentionally one-directional: this module imports the source /
adapter / constant / helper layers at module load; those layers never import
this module at module load (the few functions that need an oracle function
lazy-import from ``price_oracle`` at call time).

The public entry points re-exported through ``price_oracle.py`` keep their
exact signatures, so the ~10 callers (scanners, databus providers, tests) are
unaffected.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

if TYPE_CHECKING:
    from collections.abc import Callable

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._price_adapters import (
    _coingecko_price,
    _decimals_from_dexscreener,
    _defillama_price,
    _dexscreener_price,
    _geckoterminal_price,
    _native_coingecko,
    _native_defillama,
    _oneinch_price,
    _paraswap_price,
)
from rugmunchbot._vendor.scanner_engines._price_constants import (
    _FALLBACK_DECIMALS,
    _MEM_MAX,
    _NATIVE_TTL,
    _WRAPPED_NATIVE,
)
from rugmunchbot._vendor.scanner_engines._price_helpers import (
    _normalize_chain,
    _normalize_token,
)
from rugmunchbot._vendor.scanner_engines._sources import (
    _cache_set_price,
    _decimals_mem,
    _get_cached_price,
    _meta_get,
    _native_mem,
    _own_dex_events,
    _query_swaps_between,
    _vwap_price_from_swaps,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_token_price(
    token_address: str,
    chain: str,
    *,
    force_fresh: bool = False,
    _depth: int = 0,
) -> dict[str, Any] | None:
    """Resolve a token's USD price through the tiered oracle.

    Tier order:
      1. Caches: in-process -> Redis -> ClickHouse token_meta
      2. OWN DATA: dex_events reserve ratios (decimals-aware, USD-anchored)
      3. DexScreener (free)
      4. CoinGecko (free, rate-limited)
      5. DeFiLlama (free)
      6. GeckoTerminal (free)
      7. ParaSwap (free) / 1inch (freemium)

    Args:
        token_address: Token contract address (EVM lowercased; Solana preserved)
        chain: Chain name (ethereum, bsc, solana, ...)
        force_fresh: Bypass all caches and re-resolve
        _depth: Internal recursion guard for paired-token anchoring (max 2)

    Returns:
        {
            "price_usd": float,
            "source": str,       # own_dex_events | dexscreener | coingecko | ...
            "timestamp": float,  # epoch seconds of resolution
            "confidence": float, # 0-1
        }
        or None when every tier fails (ClickHouse unreachable, APIs down).
    """
    token = _normalize_token(token_address, chain)
    chain = _normalize_chain(chain)
    if not token:
        return None
    _start = time.monotonic()

    # ── 1. Caches ──
    if not force_fresh:
        cached = await _get_cached_price(token, chain, _depth)
        if cached:
            return cached

    # ── 2..N. Live resolution ──
    for source_name, fetcher in _TIERS:
        try:
            result = await fetcher(token, chain, _depth=_depth)
        except (
            httpx.HTTPError,
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            CHDriverError,
        ) as exc:  # defensive: a provider must never crash the oracle
            logger.debug("price_oracle tier %s failed for %s: %s", source_name, token[:16], exc)
            result = None
        if not result:
            continue
        price_usd = float(result.get("price_usd", 0) or 0)
        if price_usd <= 0:
            continue

        payload: dict[str, Any] = {
            "price_usd": price_usd,
            "source": source_name,
            "timestamp": time.time(),
            "confidence": float(result.get("confidence", 0.5) or 0.5),
        }
        # Optional enrichment when the tier provided it
        for extra in (
            "volume_24h_usd",
            "market_cap_usd",
            "price_change_24h_pct",
            "liquidity_usd",
            "volume",
            "price_change",
            "symbol",
            "name",
            "decimals",
        ):
            if result.get(extra) is not None:
                payload[extra] = result[extra]

        await _cache_set_price(token, chain, payload)
        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "price_oracle_latency",
            ms=round(_elapsed_ms, 1),
            token=token[:16],
            chain=chain,
            source=source_name,
            price_usd=round(price_usd, 10),
            depth=_depth,
        )
        return payload

    logger.info(
        "price_oracle_latency",
        ms=round((time.monotonic() - _start) * 1000, 1),
        token=token[:16],
        chain=chain,
        source="unavailable",
        price_usd=0.0,
        depth=_depth,
    )
    return None


async def get_token_price_at_time(
    token_address: str,
    chain: str,
    at_time: datetime,
    *,
    window_minutes: int = 30,
    max_swaps: int = 200,
) -> dict[str, Any] | None:
    """Resolve a token's USD price near a specific historical timestamp.

    Uses own ClickHouse ``dex_events`` when swaps exist in the window around
    ``at_time``; falls back to the current price when no historical data is
    available (the correlation engine treats a fallback with
    ``source="current_fallback"`` as lower-confidence evidence).

    Args:
        token_address: Token contract address (EVM lowercased; Solana preserved)
        chain: Chain name (ethereum, bsc, solana, ...)
        at_time: Historical timestamp to resolve price around
        window_minutes: Half-window; swaps within at_time ± window are used
        max_swaps: Maximum number of swaps to consider for the VWAP

    Returns:
        {
            "price_usd": float,
            "source": str,       # own_dex_events_at_time | current_fallback
            "timestamp": float,  # epoch seconds of the resolved price
            "confidence": float, # 0-1
            "swap_count": int,
            "window_minutes": int,
        }
        or None when every tier fails.
    """
    token = _normalize_token(token_address, chain)
    chain = _normalize_chain(chain)
    if not token:
        return None

    # Normalize the requested timestamp to epoch seconds.
    if isinstance(at_time, datetime):
        try:
            at_ts = at_time.timestamp()
        except (OverflowError, OSError, ValueError):
            at_ts = float(time.time())
    else:
        at_ts = float(at_time or time.time())

    window_secs = max(1, int(window_minutes) * 60)
    half = window_secs // 2
    lo_ts = at_ts - half
    hi_ts = at_ts + half

    swaps = await _query_swaps_between(token, chain, lo_ts, hi_ts, max_swaps=max_swaps)
    if len(swaps) >= 2:
        price = await _vwap_price_from_swaps(token, chain, swaps)
        if price is not None and price.get("price_usd", 0) > 0:
            return {
                "price_usd": float(price["price_usd"]),
                "source": "own_dex_events_at_time",
                "timestamp": float(at_ts),
                "confidence": float(price.get("confidence", 0.5)),
                "swap_count": len(swaps),
                "window_minutes": int(window_minutes),
            }

    # Fallback: current price (best effort, flagged as low confidence)
    try:
        current = await get_token_price(token, chain)
        if current and float(current.get("price_usd", 0) or 0) > 0:
            return {
                "price_usd": float(current["price_usd"]),
                "source": "current_fallback",
                "timestamp": float(at_ts),
                "confidence": 0.3,
                "swap_count": 0,
                "window_minutes": int(window_minutes),
            }
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("price_oracle at-time fallback failed for %s: %s", token[:16], exc)

    return None


async def get_token_decimals(token_address: str, chain: str) -> int | None:
    """Resolve the number of decimals for a token.

    Resolution order:
      1. In-process cache
      2. ClickHouse token_meta (decimals column)
      3. DexScreener pair response (baseToken/quoteToken decimals)
      4. Hardcoded fallback map (ETH=18, USDC=6, USDT=6, SOL=9, wrapped natives)
      5. own_dex_data.format._get_token_decimals (known tokens + estimation)
      6. Default: 9 for Solana, 18 for EVM

    Args:
        token_address: Token contract address
        chain: Chain name

    Returns:
        int decimals, or None when unresolvable (ClickHouse + APIs unreachable
        and no fallback applies).
    """
    token = _normalize_token(token_address, chain)
    chain = _normalize_chain(chain)
    if not token:
        return None

    cache_key = f"{chain}:{token}"
    if cache_key in _decimals_mem:
        return _decimals_mem[cache_key]

    # ── ClickHouse token_meta ──
    try:
        meta = await _meta_get(token, chain)
        if meta and meta.get("decimals") is not None:
            _decimals_mem[cache_key] = int(meta["decimals"])
            return int(meta["decimals"])
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("token_meta decimals read failed for %s: %s", token[:16], exc)

    # ── DexScreener pair response ──
    decimals = await _decimals_from_dexscreener(token, chain)
    if decimals is not None:
        _decimals_mem[cache_key] = decimals
        return decimals

    # ── Hardcoded fallback map ──
    chain_map = _FALLBACK_DECIMALS.get(chain, {})
    if token in chain_map:
        _decimals_mem[cache_key] = chain_map[token]
        return chain_map[token]

    # ── own_dex_data known-token table + ClickHouse estimation ──
    try:
        from rugmunchbot._vendor.scanner_engines.own_dex_data.format import _get_token_decimals

        known = _get_token_decimals(token, chain)
        if known is not None:
            _decimals_mem[cache_key] = known
            return known
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("own_dex_data decimals lookup failed for %s: %s", token[:16], exc)

    # ── Default ──
    default = 9 if chain == "solana" else 18
    _decimals_mem[cache_key] = default
    if len(_decimals_mem) > _MEM_MAX:
        _decimals_mem.pop(next(iter(_decimals_mem)), None)
    return default


async def get_native_price(chain: str) -> float:
    """Resolve the native coin price in USD for a chain (ETH, BNB, SOL, ...).

    Resolution order:
      1. In-process cache (TTL 5m)
      2. get_token_price(wrapped_native, chain)
      3. CoinGecko simple/price by native coin id
      4. DeFiLlama current price by chain id (ethereum, bsc, solana, ...)

    Returns 0.0 when every tier fails (never raises).
    """
    chain = _normalize_chain(chain)
    if chain in _native_mem:
        cached = _native_mem[chain]
        if time.time() - cached.get("_cached_at", 0) < _NATIVE_TTL:
            return float(cached.get("price_usd", 0) or 0)

    # ── Wrapped native via the token oracle ──
    wrapped = _WRAPPED_NATIVE.get(chain)
    if wrapped:
        try:
            price_result = await get_token_price(wrapped, chain, force_fresh=True)
            if price_result and price_result.get("price_usd", 0) > 0:
                price = float(price_result["price_usd"])
                _native_mem[chain] = {"price_usd": price, "_cached_at": time.time()}
                return price
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("native price via wrapped token failed for %s: %s", chain, exc)

    # ── CoinGecko by native coin id ──
    price = await _native_coingecko(chain)
    if price > 0:
        _native_mem[chain] = {"price_usd": price, "_cached_at": time.time()}
        return price

    # ── DeFiLlama native (ethereum / bsc / solana / ...) ──
    price = await _native_defillama(chain)
    if price > 0:
        _native_mem[chain] = {"price_usd": price, "_cached_at": time.time()}
        return price

    return 0.0


async def normalize_amount(token_address: str, chain: str, raw_amount: int | float) -> float | None:
    """Convert a raw on-chain amount (wei / lamports / base units) to USD.

    USD = (raw_amount / 10^decimals) * price_usd

    Args:
        token_address: Token contract address
        chain: Chain name
        raw_amount: Raw amount in the token's smallest unit

    Returns:
        USD float value, or None when the price or decimals cannot be
        resolved (or raw_amount is invalid).
    """
    try:
        raw = float(raw_amount)
    except (ValueError, TypeError):
        return None
    if raw <= 0:
        return None

    price = await get_token_price(token_address, chain)
    if not price:
        return None
    price_usd = float(price.get("price_usd", 0) or 0)
    if price_usd <= 0:
        return None

    decimals = await get_token_decimals(token_address, chain)
    if decimals is None:
        return None

    human_amount = raw / math.pow(10, decimals)
    return human_amount * price_usd


# ── Ordered fallback chain (tier order per spec) ───────────────────────────
_TIERS: list[tuple[str, Callable[..., Any]]] = [
    ("own_dex_events", _own_dex_events),
    ("dexscreener", _dexscreener_price),
    ("coingecko", _coingecko_price),
    ("defillama", _defillama_price),
    ("geckoterminal", _geckoterminal_price),
    ("paraswap", _paraswap_price),
    ("1inch", _oneinch_price),
]
