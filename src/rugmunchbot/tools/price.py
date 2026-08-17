"""price_lookup tool — REAL token price/market data via the shared pricing service.

All fallback-chain logic now lives in ONE place —
``app/domains/telegram/rugmunchbot/services/pricing.py`` (``get_price`` /
``resolve``). This module keeps only the tool-shaped wrapper:

  * the pair-normalization helpers (``_extract_pair`` / ``_extract_pairs``)
    that shape the price card contract for the chat formatter, and
  * the ``price_lookup`` handler that converts a :class:`PriceResult` into
    the ``ToolResult`` payload (query echo + pairs/quote + source label).

Fallback chain (single source of truth — see services/pricing.py):
  * address: scanner cache → backend DataBus → DexScreener → Birdeye
  * symbol:  DexScreener search → CoinGecko → CoinPaprika → CoinCap

Every number in the returned payload comes from one of those live sources.
If ALL sources fail the tool returns ``ok=False`` — the chat layer must say
"I don't have live data for that right now".
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.services.pricing import resolve
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

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
        "liquidity_usd": p.get("liquidity", {}).get("usd") if isinstance(p.get("liquidity"), dict) else None,
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


async def price_lookup(*, address: str = "", symbol: str = "", chain: str = "") -> ToolResult:
    """Resolve real price/market data for a token address or ``$SYMBOL``.

    Prefer the address path (exact). Symbol search returns the top matching
    pair from DexScreener; if that fails the fallback chain rotates through
    CoinGecko → CoinPaprika → CoinCap so prices stay available.
    """
    # The shared service runs the fallback chain (address wins when both
    # kwargs are given — the address chain is exact).
    result = await resolve(address=address, symbol=symbol, chain=chain)

    if address and result.pairs:
        pairs = _extract_pairs(result.pairs)
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_address": address, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source=result.source,
            )
    if address and result.quote is not None and result.quote.get("price_usd") is not None:
        return ToolResult(
            tool="price_lookup",
            ok=True,
            data={"query_address": address, "chain": chain or "solana", "quote": result.quote},
            symbols=[],
            addresses=[address],
            source=result.source,
        )

    if symbol and result.pairs:
        pairs = _extract_pairs(result.pairs)
        if pairs:
            return ToolResult(
                tool="price_lookup",
                ok=True,
                data={"query_symbol": symbol, "pairs": pairs},
                symbols=[p["symbol"] for p in pairs if p.get("symbol")],
                addresses=[p["address"] for p in pairs if p.get("address")],
                source=result.source,
            )
    if symbol and result.quote is not None and result.quote.get("price_usd") is not None:
        return ToolResult(
            tool="price_lookup",
            ok=True,
            data={"query_symbol": symbol, "quote": result.quote},
            symbols=[result.quote.get("symbol") or symbol],
            addresses=[],
            source=result.source,
        )

    return ToolResult(
        tool="price_lookup",
        ok=False,
        error="no live price data (backend + DexScreener + Birdeye + CoinGecko/CoinPaprika/CoinCap unavailable or no match)",
    )
