"""_local_v2.py split part: logger, _LOCAL_SCAN_TIMEOUT_S, _ENGINE_TIMEOUT_S, _MISS, _INSTALLED, _stub_module, _normalize_dex_pair, _local_dexscreener_price, _local_dex_pairs, _best_pair, _local_token_price, _local_token_metadata."""
from __future__ import annotations
from rugmunchbot.support.logging import get_logger
from typing import Any
import asyncio
import sys
import types

logger = get_logger("rmi_bot.tools.scanner.local_v2")

_LOCAL_SCAN_TIMEOUT_S = 40.0

_ENGINE_TIMEOUT_S = 12.0

_MISS: dict[str, Any] = {
    "error": "unknown_data_type",
    "message": "not available in the local scanner",
}

_INSTALLED: bool = False

def _stub_module(name: str) -> Any:
    """Create (or fetch) a stub module registered in ``sys.modules``.

    Returns ``Any`` because stub modules are built dynamically (attributes
    are assigned after creation); mypy cannot know them statically.
    """
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod

def _normalize_dex_pair(pair: dict[str, Any], token: str) -> dict[str, Any]:
    """Ensure ``token`` is the pair's ``baseToken`` (swap when it is the quote)."""
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    if not isinstance(base, dict) or not isinstance(quote, dict):
        return pair
    token_addr = str(token).lower()
    base_addr = str(base.get("address") or "").lower()
    quote_addr = str(quote.get("address") or "").lower()
    if base_addr == token_addr or quote_addr != token_addr:
        return pair
    swapped = dict(pair)
    swapped["baseToken"] = quote
    swapped["quoteToken"] = base
    try:
        price_usd = float(pair.get("priceUsd") or 0)
        price_native = float(pair.get("priceNative") or 0)
        if price_native > 0:
            swapped["priceUsd"] = price_usd / price_native
            swapped["priceNative"] = 1.0 / price_native
    except (TypeError, ValueError, ZeroDivisionError) as e:
        logger.debug("pair_swap:price_recompute_failed err=%s", e)
    return swapped

async def _local_dexscreener_price(mint: str, token: str | None = None) -> dict[str, Any] | None:
    """Local raw-DexScreener provider (the backend's is not vendored).

    Returns ``{"pairs": [...]}`` or None — the shape the price oracle tier 2
    and ``_data_sources.get_dexscreener_pairs`` expect. Pairs are normalized
    so the queried token is ALWAYS the ``baseToken`` (the backend provider
    does the same): when the token appears on the quote side of a pair
    (stablecoins like USDC are the quote on most pairs), the sides are
    swapped and the price fields inverted — otherwise the oracle's identity
    read (``baseToken.name/symbol``) returns the OTHER side's name. Fail-open.
    """
    target = (token or mint or "").strip()
    if not target:
        return None
    try:
        from rugmunchbot.tools._client import http_get_json

        body = await asyncio.wait_for(
            http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{target}", timeout=8.0),
            timeout=10.0,
        )
        if not isinstance(body, dict):
            return None
        pairs = body.get("pairs")
        if isinstance(pairs, list):
            body["pairs"] = [_normalize_dex_pair(p, target) for p in pairs if isinstance(p, dict)]
        return body
    except Exception as exc:
        logger.debug("local_dexscreener_failed addr=%.40s err=%s", target[:40], str(exc)[:80])
        return None

async def _local_dex_pairs(token: str, chain: str) -> list[dict[str, Any]]:
    """Best-effort DexScreener-compatible pair list (own ClickHouse first)."""
    try:
        from rugmunch_scanner.unified.engines.own_dex_data import get_own_dex_data

        dex = await asyncio.wait_for(get_own_dex_data(token, chain), timeout=_ENGINE_TIMEOUT_S)
        pairs = (dex or {}).get("pairs") or []
        if pairs:
            return [p for p in pairs if isinstance(p, dict)]
    except Exception as exc:
        logger.debug("local_dex_data_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
    # Fallback: raw DexScreener API (provider stub above / _data_sources).
    try:
        from rugmunch_scanner.unified.unified._data_sources import get_dexscreener_pairs

        raw = await asyncio.wait_for(get_dexscreener_pairs(token), timeout=10.0)
        pairs = (raw or {}).get("pairs") or []
        return [p for p in pairs if isinstance(p, dict)]
    except Exception:
        return []

def _best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest-liquidity pair (mirrors the bot's ``_ds_market_snapshot`` pick)."""
    if not pairs:
        return None
    try:
        return max(
            pairs,
            key=lambda p: (
                float(((p.get("liquidity") or {}).get("usd")) or 0)
                + float(((p.get("volume") or {}).get("h24")) or 0) * 0.01
            ),
        )
    except (TypeError, ValueError):
        return pairs[0]

async def _local_token_price(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``token_price`` — tiered price oracle + own DEX pairs."""
    if not token:
        return _MISS
    payload: dict[str, Any] = {}
    try:
        from rugmunch_scanner.unified.engines.price_oracle import get_token_price

        price = await asyncio.wait_for(get_token_price(token, chain), timeout=12.0)
        if isinstance(price, dict) and price.get("price_usd") is not None:
            payload["price_usd"] = price.get("price_usd")
            payload["source"] = f"local:{price.get('source', 'price_oracle')}"
            payload["timestamp"] = price.get("timestamp")
            payload["confidence"] = price.get("confidence")
    except Exception as exc:
        logger.debug("local_price_oracle_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
    pairs = await _local_dex_pairs(token, chain)
    best = _best_pair(pairs)
    if best:
        base = best.get("baseToken") or {}
        payload.setdefault("name", base.get("name") or "")
        payload.setdefault("symbol", base.get("symbol") or "")
        if best.get("marketCap") is not None:
            payload.setdefault("market_cap_usd", best.get("marketCap"))
        liq = (best.get("liquidity") or {}).get("usd")
        if liq is not None:
            payload.setdefault("liquidity_usd", liq)
        vol = (best.get("volume") or {}).get("h24")
        if vol is not None:
            payload.setdefault("volume_24h_usd", vol)
        if best.get("fdv") is not None and "market_cap_usd" not in payload:
            payload.setdefault("market_cap_usd", best.get("fdv"))
    if not payload:
        return _MISS
    return {
        "data": payload,
        "source": payload.get("source", "local:price_oracle"),
        "tier": "free",
        "is_local": True,
    }

async def _local_token_metadata(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``token_metadata`` — DexScreener-compatible pairs payload."""
    if not token:
        return _MISS
    pairs = await _local_dex_pairs(token, chain)
    if not pairs:
        return _MISS
    best = _best_pair(pairs)
    base = best.get("baseToken") or {} if best else {}
    return {
        "data": {
            "pairs": pairs,
            "name": (base.get("name") or "") if isinstance(base, dict) else "",
            "symbol": (base.get("symbol") or "") if isinstance(base, dict) else "",
        },
        "source": "local:own_dex_data",
        "tier": "free",
        "is_local": True,
    }
