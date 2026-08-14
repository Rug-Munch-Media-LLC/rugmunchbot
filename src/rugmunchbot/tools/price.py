"""price_lookup tool — REAL token price/market data with a rotating fallback chain.

Sources (in order — rotate until one returns data so prices are available
at all times):
  1. Our backend ``/api/v1/databus/token/{address}`` — DataBus aggregate
     (DexScreener + Birdeye + GMGN + our scanners), cached by the backend.
  2. DexScreener token endpoint (``/tokens/{address}``) — direct, free.
  3. Birdeye ``/defi/price`` (direct, key from env/gopass) — token price.
  4. CoinGecko ``/search`` + ``/coins/{id}`` (free + optional demo key).
  5. CoinPaprika ``/search`` + ``/tickers/{id}`` — free, no key.
  6. CoinCap ``/assets`` search — free, no key.

Every number in the returned payload comes from one of those live sources.
If ALL sources fail the tool returns ``ok=False`` — the chat layer must say
"I don't have live data for that right now".
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import DEXSCREENER
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.birdeye import birdeye_price
from rugmunchbot.tools.market import symbol_quote_fallback
from rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.price")


def _extract_pair(p: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a DexScreener pair into the price card contract."""
    base = p.get("baseToken") or {}
    addr = base.get("address", "")
    if not addr:
        return None
    return {
        "address": addr,
        "symbol": base.get("symbol", ""),
        "name": base.get("name", ""),
        "chain": p.get("chainId", ""),
        "dex": p.get("dexId", ""),
        "price_usd": p.get("priceUsd"),
        "price_native": p.get("priceNative"),
        "change_1h": (p.get("priceChange") or {}).get("h1"),
        "change_6h": (p.get("priceChange") or {}).get("h6"),
        "change_24h": (p.get("priceChange") or {}).get("h24"),
        "volume_24h": (p.get("volume") or {}).get("h24"),
        "liquidity_usd": p.get("liquidity", {}).get("usd")
        if isinstance(p.get("liquidity"), dict)
        else None,
        "market_cap": p.get("marketCap"),
        "fdv": p.get("fdv"),
        "pair_address": p.get("pairAddress"),
    }


def _extract_pairs(items: object) -> list[dict[str, Any]]:
    """Extract valid pair dicts from a raw JSON value (None results dropped).

    Mirrors ``[_extract_pair(p) for p in items if isinstance(p, dict)]`` with
    the ``None`` results filtered out — mypy-clean and crash-safe.
    """
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for p in items:
        if isinstance(p, dict):
            pair = _extract_pair(p)
            if pair is not None:
                out.append(pair)
    return out


def _parse_backend_detail(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the DataBus ``/token/{address}`` payload into pairs list."""
    pairs = body.get("pairs")
    if isinstance(pairs, list):
        return _extract_pairs(pairs)
    # Some payloads nest under ``data``.
    data = body.get("data")
    if isinstance(data, dict):
        pairs = data.get("pairs")
        if isinstance(pairs, list):
            return _extract_pairs(pairs)
    return []


async def price_lookup(*, address: str = "", symbol: str = "", chain: str = "") -> ToolResult:
    """Resolve real price/market data for a token address or ``$SYMBOL``.

    Prefer the address path (exact). Symbol search returns the top matching
    pair from DexScreener; if that fails the fallback chain rotates through
    Birdeye → CoinGecko → CoinPaprika → CoinCap so prices stay available.
    """
    if address:
        # 1. Our backend DataBus aggregate (own data first).
        body = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=6.0)
        pairs = _parse_backend_detail(body) if body else []
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_address": address, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source="backend:/api/v1/databus/token",
            )
        # 2. Direct DexScreener token endpoint (address is exact).
        body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
        pairs = _extract_pairs((body or {}).get("pairs", []))
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_address": address, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source="dexscreener:/tokens/{address}",
            )
        # 3. Birdeye direct price (key from env/gopass; rotates on failure).
        quote = await birdeye_price(address, chain or "solana")
        if quote and quote.get("price_usd") is not None:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_address": address, "chain": chain or "solana", "quote": quote},
                symbols=[],
                addresses=[address],
                source="birdeye:/defi/price",
            )

    if symbol:
        q = symbol.strip().upper()
        body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
        pairs = _extract_pairs((body or {}).get("pairs", []))
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_symbol": symbol, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source="dexscreener:/search",
            )
        # 4-6. CoinGecko → CoinPaprika → CoinCap rotation for $SYMBOL queries.
        quote = await symbol_quote_fallback(q)
        if quote and quote.get("price_usd") is not None:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_symbol": symbol, "quote": quote},
                symbols=[quote.get("symbol") or symbol],
                addresses=[],
                source=f"{quote.get('source', 'fallback')}",
            )

    return ToolResult(
        tool="price_lookup",
        ok=False,
        error="no live price data (backend + DexScreener + Birdeye + CoinGecko/CoinPaprika/CoinCap unavailable or no match)",
    )
