# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/_data_sources.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Shared data-source getters — single source of truth per data type.

Consolidation wave (2026-08-08): the scanner pipeline had accumulated
duplicated data paths — price fetched from DexScreener in 3 places,
liquidity from 2, holder data from 2. This module is the ONE thin entry
point that secondary engines call instead of crafting their own external
API fetches.

Data hierarchy (Layer 1 → Layer 4, most preferred first):

    Layer 1 — ClickHouse indexer (our data)
        ``evm_transfers`` / ``dex_events`` / ``deployer_events``.
        Own-data engines compute from here at zero cost:
        ``own_dex_data`` (liquidity), ``own_holder_analysis`` (holders),
        ``price_oracle`` tier 1 (dex_events TWAP).
    Layer 2 — Scanner cached results (Redis + in-process)
        ``price_oracle`` (Redis ``price:{chain}:{token}`` + in-process),
        ``own_dex_data`` ``_DEX_CACHE``, ``own_holder_analysis``
        ``_HOLDER_CACHE``. The getters below delegate to the canonical
        engines, whose caches stay authoritative.
    Layer 3 — DataBus providers
        ``databus.providers.chains.dexscreener`` (raw pair payloads,
        used only for deployer/creator resolution where own data does not
        carry ``creatorAddress``), ``free_security`` (CoinGecko).
    Layer 4 — External APIs (DexScreener, CoinGecko, DeFiLlama, ...)
        Reached ONLY through the canonical engines or the DataBus
        providers above. Engines must not open their own httpx calls to
        these hosts; use the getters here.

Canonical source per data type:

    - Token price    → ``engines.price_oracle.get_token_price``
                       (tiered: ClickHouse TWAP → DexScreener → CoinGecko
                       → DeFiLlama → GeckoTerminal → ParaSwap/1inch)
    - Token liquidity → ``engines.own_dex_data.get_own_dex_data``
                       (ClickHouse dex_events, DexScreener fallback)
    - Holder data    → ``engines.own_holder_analysis.get_own_holder_analysis``
                       (ClickHouse evm_transfers)
    - Raw DexScreener pairs (deployer/creator discovery) → DataBus
                       ``dexscreener._dexscreener_price`` via
                       ``get_dexscreener_pairs`` — own data does not carry
                       ``creatorAddress``, so raw pair payloads stay the
                       canonical source for that one field.

All getters are behavior-preserving: they return exactly the payload the
canonical engine returns, so existing callers keep their extraction logic.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Thin cache for the raw DexScreener pair payload ───────────────────────
# The canonical engines (price_oracle / own_dex_data / own_holder_analysis)
# own their own caches, so we only memoize the DataBus pair payload, which
# has no cache of its own and is requested once per token per scan.
_DEXSCREENER_PAIRS_CACHE: dict[str, dict[str, Any] | None] = {}
_DEXSCREENER_PAIRS_TTL = 60  # seconds — raw pairs change slowly
_DEXSCREENER_PAIRS_MAX = 200


async def get_token_price(
    token_address: str,
    chain: str,
    *,
    force_fresh: bool = False,
    _depth: int = 0,
) -> dict[str, Any] | None:
    """Canonical token price — tiered oracle (ClickHouse TWAP → free APIs).

    Layer 1 → 4: own dex_events TWAP first, then DexScreener / CoinGecko /
    DeFiLlama / GeckoTerminal / ParaSwap / 1inch. Cached in Redis + in-process
    by the canonical engine.

    Returns the ``price_oracle.get_token_price`` payload
    (``price_usd`` / ``source`` / ``timestamp`` / ``confidence``) or None.
    """
    from rugmunchbot._vendor.scanner_engines.price_oracle import get_token_price as _canonical

    return await _canonical(
        token_address,
        chain,
        force_fresh=force_fresh,
        _depth=_depth,
    )


async def get_token_liquidity(token_address: str, chain: str) -> dict[str, Any]:
    """Canonical token liquidity + DEX pair data — own ClickHouse first.

    Layer 1 → 4: ``own_dex_data.get_own_dex_data`` queries ClickHouse
    ``dex_events`` and falls back to the DexScreener API when fewer than 3
    swaps are present. Cached by the canonical engine (``_DEX_CACHE``).

    Returns the DexScreener-compatible payload
    (``pairs`` / ``source`` / ``metrics``), e.g. callers read
    ``payload["pairs"][0]["liquidity"]["usd"]`` — identical to the shape
    ``modules._core._fetch_dexscreener`` used to return.
    """
    from rugmunchbot._vendor.scanner_engines.own_dex_data import get_own_dex_data as _canonical

    return await _canonical(token_address, chain)


async def get_token_holders(token_address: str, chain: str) -> dict[str, Any]:
    """Canonical holder analysis — own ClickHouse evm_transfers.

    Layer 1: ``own_holder_analysis.get_own_holder_analysis`` computes
    holder distribution from ``evm_transfers``. Cached by the canonical
    engine (``_HOLDER_CACHE``, TTL 300s).

    Returns the holder-analysis payload
    (``holder_count`` / ``top_holders`` / ``concentration_pct`` /
    ``gini_coefficient`` / ... ) with ``source == "own"``.
    """
    from rugmunchbot._vendor.scanner_engines.own_holder_analysis import (
        get_own_holder_analysis as _canonical,
    )

    return await _canonical(token_address, chain)


async def get_dexscreener_pairs(token_address: str) -> dict[str, Any] | None:
    """Raw DexScreener pair payload — deployer/creator discovery only.

    Layer 3 → 4: DataBus ``dexscreener`` provider (``/latest/dex/tokens/..``).
    Own-data engines do not carry ``creatorAddress``, so the raw pair payload
    stays the canonical source for deployer/creator resolution (e.g.
    ``unified/sentinel_phase._resolve_deployer``).

    Returns the raw DexScreener JSON (``{"pairs": [...]}``) or None. Memoized
    in-process for ``_DEXSCREENER_PAIRS_TTL`` seconds.
    """
    if not token_address:
        return None
    key = token_address.lower()
    cached = _DEXSCREENER_PAIRS_CACHE.get(key)
    if cached is not None and time.time() - cached.get("_cached_at", 0) < _DEXSCREENER_PAIRS_TTL:
        return cached.get("payload")
    try:
        from app.domains.databus.providers.chains.dexscreener import _dexscreener_price as _provider

        raw = await _provider(mint=token_address, token=token_address)
    except Exception as exc:  # defensive: a provider must never crash callers
        logger.debug("dexscreener_pairs_failed %s: %s", token_address[:16], exc)
        raw = None

    if len(_DEXSCREENER_PAIRS_CACHE) >= _DEXSCREENER_PAIRS_MAX:
        _DEXSCREENER_PAIRS_CACHE.pop(next(iter(_DEXSCREENER_PAIRS_CACHE)), None)
    _DEXSCREENER_PAIRS_CACHE[key] = {"payload": raw, "_cached_at": time.time()}
    return raw


def clear_cache() -> None:
    """Clear the raw DexScreener pair memo (canonical engines own their caches)."""
    _DEXSCREENER_PAIRS_CACHE.clear()


def get_cache_stats() -> dict[str, Any]:
    """Cache stats for the raw DexScreener pair memo."""
    return {
        "dexscreener_pairs_cache_size": len(_DEXSCREENER_PAIRS_CACHE),
        "dexscreener_pairs_cache_max": _DEXSCREENER_PAIRS_MAX,
        "dexscreener_pairs_cache_ttl": _DEXSCREENER_PAIRS_TTL,
    }
