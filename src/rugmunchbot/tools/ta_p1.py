"""ta.py split part: logger, _GECKO_NETWORKS, _CHAIN_ALIASES, _GECKO_OHLCV_URL, _Candle, _CandleResult, _to_float, _normalize_chain, _normalize_pairs, _is_pair, _pick_best_pair, _resolve_pair, _pair_snapshot, _market_context."""
from __future__ import annotations
from rugmunchbot.config import DEXSCREENER
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json
from typing import Any

logger = get_logger("rmi_bot.tools.ta")

_GECKO_NETWORKS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "tron": "tron",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "optimism": "optimism",
}

_CHAIN_ALIASES: dict[str, str] = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "0x1": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "binance-smart-chain": "bsc",
    "arb": "arbitrum",
    "arbitrum": "arbitrum",
    "base": "base",
    "polygon": "polygon",
    "matic": "polygon",
    "polygon-pos": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avax": "avalanche",
    "avalanche": "avalanche",
    "tron": "tron",
    "trx": "tron",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "zksync": "zksync",
    "scroll": "scroll",
    "mantle": "mantle",
    "sei": "sei",
    "sui": "sui",
    "aptos": "aptos",
    "injective": "injective",
    "berachain": "berachain",
    "bera": "berachain",
    "hyperliquid": "hyperliquid",
    "mode": "mode",
    "fantom": "fantom",
    "ftm": "fantom",
}

_GECKO_OHLCV_URL = "https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair}/ohlcv/hour"

_Candle = dict[str, Any]

_CandleResult = tuple[list[_Candle], str]

def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _normalize_chain(chain: str) -> str:
    key = (chain or "").strip().lower()
    return _CHAIN_ALIASES.get(key, key)

def _normalize_pairs(data: Any) -> list[dict[str, Any]]:
    """Normalize DexScreener pair payloads (dict {"pairs": [...]} or list)."""
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
    elif isinstance(data, list):
        pairs = data
    else:
        pairs = []
    return [p for p in pairs if isinstance(p, dict)]

def _is_pair(p: dict[str, Any]) -> bool:
    return bool(p.get("pairAddress")) and isinstance(p.get("baseToken"), dict)

def _pick_best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest-24h-volume pair (real volume from the source)."""
    best: dict[str, Any] | None = None
    best_vol = -1.0
    for p in pairs:
        vol = _to_float((p.get("volume") or {}).get("h24")) or 0.0
        if vol > best_vol:
            best_vol = vol
            best = p
    return best

async def _resolve_pair(address: str, symbol: str, chain: str) -> dict[str, Any] | None:
    """Resolve a DexScreener pair for an address or ``$SYMBOL``.

    Address queries use the exact token endpoint; symbol queries use search
    and prefer a pair whose baseToken symbol matches exactly. ``chain`` (when
    given) filters pairs; the winner is the highest-24h-volume pair.
    """
    if address:
        body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
        pairs = [p for p in _normalize_pairs(body) if _is_pair(p)]
        if chain:
            want = _normalize_chain(chain)
            matched = [p for p in pairs if _normalize_chain(str(p.get("chainId") or "")) == want]
            if matched:
                pairs = matched
        return _pick_best_pair(pairs)

    q = (symbol or "").strip().upper()
    if not q:
        return None
    body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
    pairs = [p for p in _normalize_pairs(body) if _is_pair(p)]
    if not pairs:
        return None
    exact = [p for p in pairs if ((p.get("baseToken") or {}).get("symbol") or "").upper() == q]
    if chain:
        want = _normalize_chain(chain)
        exact = [p for p in exact if _normalize_chain(str(p.get("chainId") or "")) == want] or exact
    return _pick_best_pair(exact or pairs)

def _pair_snapshot(pair: dict[str, Any], address: str, symbol: str) -> dict[str, Any]:
    """Real DexScreener pair metrics (never synthesized)."""
    base = pair.get("baseToken") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}
    liquidity_raw = pair.get("liquidity")
    liquidity = liquidity_raw if isinstance(liquidity_raw, dict) else {}
    return {
        "address": address or base.get("address") or "",
        "symbol": (base.get("symbol") or "").strip().upper() or (symbol or "").strip().upper(),
        "name": (base.get("name") or "").strip(),
        "chain": _normalize_chain(str(pair.get("chainId") or "")),
        "dex": pair.get("dexId") or "",
        "pair_address": pair.get("pairAddress") or "",
        "price_usd": _to_float(pair.get("priceUsd")),
        "change_1h": _to_float(price_change.get("h1")),
        "change_6h": _to_float(price_change.get("h6")),
        "change_24h": _to_float(price_change.get("h24")),
        "volume_24h": _to_float(volume.get("h24")),
        "liquidity_usd": _to_float(liquidity.get("usd")),
        "market_cap": _to_float(pair.get("marketCap")),
        "fdv": _to_float(pair.get("fdv")),
    }

def _market_context(snapshot: dict[str, Any], analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Flat live-market read merged from the real DexScreener pair snapshot
    and (when candles were available) the candle analysis.

    Only real source values are included — ``None`` fields are omitted so the
    LLM never sees fabricated numbers. ``distance_from_window_*`` is measured
    against the candle WINDOW high/low, deliberately NOT an all-time high: no
    live ATH feed exists for arbitrary DEX pairs, and the key stays honest so
    the model phrases it as "recent range" rather than "ATH".
    """
    ctx: dict[str, Any] = {}
    for key, value in (
        ("price_usd", snapshot.get("price_usd")),
        ("change_1h", snapshot.get("change_1h")),
        ("change_6h", snapshot.get("change_6h")),
        ("change_24h", snapshot.get("change_24h")),
        ("volume_24h", snapshot.get("volume_24h")),
        ("liquidity_usd", snapshot.get("liquidity_usd")),
        ("market_cap", snapshot.get("market_cap")),
        ("fdv", snapshot.get("fdv")),
    ):
        if value is not None:
            ctx[key] = value
    if analysis is not None:
        if analysis.get("change_pct") is not None:
            ctx["change_window_pct"] = analysis.get("change_pct")
        if analysis.get("high") is not None:
            ctx["window_high"] = analysis.get("high")
        if analysis.get("low") is not None:
            ctx["window_low"] = analysis.get("low")
        price = analysis.get("price")
        high = analysis.get("high")
        low = analysis.get("low")
        if price is not None and high and high > 0:
            ctx["distance_from_window_high_pct"] = (high - price) / high * 100
        if price is not None and low and low > 0:
            ctx["distance_from_window_low_pct"] = (price - low) / low * 100
    return ctx
