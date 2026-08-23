# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/_price_adapters.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Free API price adapters — DexScreener / CoinGecko / DeFiLlama / GeckoTerminal / ParaSwap / 1inch.

Extracted from ``engines/price_oracle.py`` (degodding wave 4, 2026-08-08) so
the external price fetchers live beside the other pure adapter helpers instead
of bloating the oracle's tier resolution / ClickHouse / cache logic.

This module is the ``FREE API TIERS`` + ``NATIVE PRICE FALLBACKS`` section of
the oracle. Every function is defensive: type-hinted, docstringed, wrapped in
try/except, and fails gracefully (return ``None`` / ``0.0``) when the free
APIs are unreachable.

Imports are intentionally one-directional: ``price_oracle`` imports these
adapters; the adapters never import ``price_oracle`` at module load. The two
functions that need the native price (``_paraswap_price``, ``_oneinch_price``)
lazy-import ``get_native_price`` at call time, matching the established lazy
import pattern used for the databus providers.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Chain normalization ────────────────────────────────────────────────────
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
    "linea": "linea",
    "zksync": "zksync",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "scroll": "scroll",
    "mantle": "mantle",
    "solana": "solana",
}

# ── Native coin IDs for CoinGecko simple/price fallback (no key required) ──
_NATIVE_COINGECKO_IDS: dict[str, str] = {
    "ethereum": "ethereum",
    "bsc": "binancecoin",
    "polygon": "matic-network",
    "arbitrum": "ethereum",
    "optimism": "ethereum",
    "base": "ethereum",
    "avalanche": "avalanche-2",
    "fantom": "fantom",
    "linea": "ethereum",
    "gnosis": "xdai",
    "mantle": "mantle",
    "solana": "solana",
}


async def _dexscreener_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 2 — DexScreener token price (free, 300 req/min, no auth)."""
    try:
        from app.domains.databus.providers.chains.dexscreener import _dexscreener_price as _ds

        raw = await _ds(mint=token, token=token)
        if not raw:
            return None
        pairs = raw.get("pairs") if isinstance(raw, dict) else None
        if not pairs:
            return None

        chain_map = _CHAIN_MAP.get(chain, chain)
        best_pair: dict[str, Any] | None = None
        best_liquidity = -1.0
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            # Skip pairs from wrong chains (e.g., USDC on pulsechain when querying ethereum)
            pair_chain = (pair.get("chainId") or "").lower()
            if pair_chain and pair_chain != chain_map.lower():
                continue
            liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            if liq > best_liquidity:
                best_liquidity = liq
                best_pair = pair
        if best_pair is None:
            return None

        price_usd = _pair_price_for_token(best_pair, token)
        if price_usd <= 0:
            return None

        # Surface the best-pair market snapshot. ``best_liquidity`` is already
        # computed above for pair selection — it MUST also flow through the
        # oracle payload, else the report's Liquidity row reads 0 even though
        # the pair data carried it (fix 2026-08-16). Timeframe volume/price
        # change (DexScreener ``volume.h1/h6/h24`` + ``priceChange.*``) are
        # carried verbatim so downstream consumers can render real
        # multi-timeframe figures instead of zeros.
        result: dict[str, Any] = {
            "price_usd": price_usd,
            "confidence": 0.7,
            "volume_24h_usd": float(best_pair.get("volume", {}).get("h24", 0) or 0),
            "market_cap_usd": float(best_pair.get("fdv", 0) or 0) or None,
            "price_change_24h_pct": float(best_pair.get("priceChange", {}).get("h24", 0) or 0),
            "liquidity_usd": best_liquidity if best_liquidity > 0 else None,
            "volume": {
                tf: (float(v) if isinstance(v, (int, float)) else None)
                for tf, v in (best_pair.get("volume") or {}).items()
                if tf in ("m5", "h1", "h6", "h24")
            },
            "price_change": {
                tf: (float(v) if isinstance(v, (int, float)) else None)
                for tf, v in (best_pair.get("priceChange") or {}).items()
                if tf in ("m1", "m5", "h1", "h6", "h24")
            },
        }
        decimals = _pair_decimals_for_token(best_pair, token)
        if decimals is not None:
            result["decimals"] = decimals
        base = best_pair.get("baseToken") or {}
        if isinstance(base, dict) and base.get("symbol"):
            result["symbol"] = str(base.get("symbol") or "")
        if isinstance(base, dict) and base.get("name"):
            result["name"] = str(base.get("name") or "")
        return result
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("price_oracle DexScreener failed for %s: %s", token[:16], exc)
        return None


def _pair_price_for_token(pair: dict[str, Any], token: str) -> float:
    """Extract the USD price of `token` from a DexScreener pair object.

    DexScreener semantics: priceUsd = USD per 1 base token; priceNative =
    quote units per 1 base token. When our token is the quote side, the
    USD price is priceUsd / priceNative.
    """
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    if not isinstance(base, dict) or not isinstance(quote, dict):
        return 0.0

    base_addr = str(base.get("address") or "").lower()
    quote_addr = str(quote.get("address") or "").lower()
    token_addr = str(token).lower()

    try:
        price_usd = float(pair.get("priceUsd") or 0)
        price_native = float(pair.get("priceNative") or 0)
    except (ValueError, TypeError):
        return 0.0

    if base_addr == token_addr:
        return price_usd
    if quote_addr == token_addr and price_native > 0:
        return price_usd / price_native
    return 0.0


def _pair_decimals_for_token(pair: dict[str, Any], token: str) -> int | None:
    """Return the decimals of `token` from a DexScreener pair (if present)."""
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    token_addr = str(token).lower()
    for side in (base, quote):
        if not isinstance(side, dict):
            continue
        if str(side.get("address") or "").lower() == token_addr and side.get("decimals") is not None:
            try:
                return int(side["decimals"])
            except (ValueError, TypeError):
                return None
    return None


async def _decimals_from_dexscreener(token: str, chain: str) -> int | None:
    """Best-effort decimals resolution from a DexScreener pair response."""
    price_result = await _dexscreener_price(token, chain)
    if price_result and price_result.get("decimals") is not None:
        return int(price_result["decimals"])
    return None


async def _coingecko_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 3 — CoinGecko simple/token_price (free, 10-30 req/min).

    Reuses the verified free_security provider (same endpoint, no auth).
    """
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_coingecko

        return await _free_market_coingecko(token_address=token, chain=chain, address=token)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("price_oracle CoinGecko failed for %s: %s", token[:16], exc)
        return None


async def _defillama_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 4 — DeFiLlama current price (free, no auth).

    https://coins.llama.fi/prices/current/{chain}:{address}
    """
    prefix_map = {
        "ethereum": "ethereum",
        "bsc": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "base": "base",
        "avalanche": "avalanche",
        "fantom": "fantom",
        "linea": "linea",
        "gnosis": "xdai",
        "mantle": "mantle",
        "solana": "solana",
    }
    prefix = prefix_map.get(chain)
    if not prefix:
        return None

    try:
        import httpx

        llama_id = f"{prefix}:{token}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://coins.llama.fi/prices/current/{llama_id}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            coins = data.get("coins", {}) if isinstance(data, dict) else {}
            coin_data = coins.get(llama_id) or coins.get(token)
            if not isinstance(coin_data, dict):
                return None
            price = float(coin_data.get("price", 0) or 0)
            if price <= 0:
                return None
            result: dict[str, Any] = {
                "price_usd": price,
                "confidence": float(coin_data.get("confidence", 0.5) or 0.5),
            }
            if coin_data.get("volume24h") is not None:
                result["volume_24h_usd"] = float(coin_data.get("volume24h") or 0)
            if coin_data.get("marketCap") is not None:
                result["market_cap_usd"] = float(coin_data.get("marketCap") or 0)
            return result
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError, httpx.HTTPError) as exc:
        logger.debug("price_oracle DeFiLlama failed for %s: %s", token[:16], exc)
        return None


async def _geckoterminal_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 5 — GeckoTerminal token price (free, no auth).

    Reuses the verified free_security provider (same endpoint).
    """
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_geckoterminal

        return await _free_market_geckoterminal(token_address=token, chain=chain, address=token)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("price_oracle GeckoTerminal failed for %s: %s", token[:16], exc)
        return None


async def _paraswap_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 6a — ParaSwap price quote (free, no auth).

    Reuses the verified free_security provider which sells 1 native unit
    for the token; the resulting token-per-native ratio is converted to
    USD via get_native_price.
    """
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_paraswap_quote

        result = await _free_market_paraswap_quote(token_address=token, chain=chain, address=token)
        if not result:
            return None
        src_amount = float(result.get("src_amount", 0) or 0)
        dest_amount = float(result.get("dest_amount", 0) or 0)
        if src_amount <= 0 or dest_amount <= 0:
            return None
        from rugmunchbot._vendor.scanner_engines.price_oracle import get_native_price

        native_price = await get_native_price(chain)
        if native_price <= 0:
            return None
        # src = native amount, dest = token amount for 1 native
        # token_price_usd = (native_per_token) * native_usd
        token_price_usd = (src_amount / dest_amount) * native_price
        return {"price_usd": token_price_usd, "confidence": 0.55}
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("price_oracle ParaSwap failed for %s: %s", token[:16], exc)
        return None


async def _oneinch_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 6b — 1inch quote (FREEMIUM; only fires when ONEINCH_API_KEY is set)."""
    api_key = os.getenv("ONEINCH_API_KEY", "")
    if not api_key:
        return None
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_oneinch_quote

        result = await _free_market_oneinch_quote(
            token_address=token,
            chain=chain,
            address=token,
            api_key=api_key,
        )
        if not result:
            return None
        src_amount = float(result.get("src_amount", 0) or 0)
        dest_amount = float(result.get("dest_amount", 0) or 0)
        if src_amount <= 0 or dest_amount <= 0:
            return None
        from rugmunchbot._vendor.scanner_engines.price_oracle import get_native_price

        native_price = await get_native_price(chain)
        if native_price <= 0:
            return None
        token_price_usd = (src_amount / dest_amount) * native_price
        return {"price_usd": token_price_usd, "confidence": 0.5}
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("price_oracle 1inch failed for %s: %s", token[:16], exc)
        return None


async def _native_coingecko(chain: str) -> float:
    """CoinGecko simple/price by native coin id (free, no auth)."""
    coin_id = _NATIVE_COINGECKO_IDS.get(chain)
    if not coin_id:
        return 0.0
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
            )
            if resp.status_code != 200:
                return 0.0
            data = resp.json()
            coin = data.get(coin_id) if isinstance(data, dict) else None
            if isinstance(coin, dict):
                return float(coin.get("usd", 0) or 0)
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError, httpx.HTTPError) as exc:
        logger.debug("price_oracle native CoinGecko failed for %s: %s", chain, exc)
    return 0.0


async def _native_defillama(chain: str) -> float:
    """DeFiLlama native coin price by chain id (free, no auth)."""
    prefix = {
        "ethereum": "ethereum",
        "bsc": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "base": "base",
        "avalanche": "avalanche",
        "fantom": "fantom",
        "solana": "solana",
    }.get(chain)
    if not prefix:
        return 0.0
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://coins.llama.fi/prices/current/{prefix}")
            if resp.status_code != 200:
                return 0.0
            data = resp.json()
            coins = data.get("coins", {}) if isinstance(data, dict) else {}
            coin = coins.get(prefix) or coins.get(f"coingecko:{prefix}")
            if isinstance(coin, dict):
                return float(coin.get("price", 0) or 0)
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError, httpx.HTTPError) as exc:
        logger.debug("price_oracle native DeFiLlama failed for %s: %s", chain, exc)
    return 0.0
