"""trending tool — REAL currently-trending tokens.

Sources (in order):
  1. Our backend ``/api/v1/market/trending`` — fused RugCharts + DexScreener
     + CoinGecko trending (own-data first, backend-cached).
  2. Backend ``/api/v1/news/trending`` — trending news headlines.
  3. DexScreener token-boosts (``/token-boosts/top/v1``) — direct, free.

All token symbols/addresses in the result come from those live sources.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import DEXSCREENER_TOKEN_BOOSTS
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.trending")


async def _fetch_market_trending(limit: int = 10) -> tuple[list[dict[str, Any]], str] | None:
    body = await backend_json_get("/api/v1/market/trending", params={"limit": limit}, timeout=6.0)
    if not body:
        return None
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return None
    tokens: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tokens.append(
            {
                "symbol": it.get("symbol") or "",
                "name": it.get("name") or "",
                "address": it.get("address") or it.get("token_address") or "",
                "chain": it.get("chain") or "",
                "price_usd": it.get("price"),
                "change_24h": it.get("change_24h"),
                "volume_24h": it.get("volume_24h"),
                "market_cap": it.get("market_cap"),
                "rank": it.get("rank"),
                "source": it.get("source") or "backend",
            }
        )
    if not tokens:
        return None
    return tokens, "backend:/api/v1/market/trending"


async def _fetch_token_boosts(limit: int = 10) -> tuple[list[dict[str, Any]], str] | None:
    body = await http_get_json(DEXSCREENER_TOKEN_BOOSTS, timeout=6.0)
    if not isinstance(body, list) or not body:
        return None
    tokens: list[dict[str, Any]] = []
    for it in body[:limit]:
        if not isinstance(it, dict):
            continue
        tokens.append(
            {
                "symbol": (it.get("description") or "").split(".")[0].replace("$", "").strip(),
                "name": (it.get("description") or "").split(".")[0].replace("$", "").strip(),
                "address": it.get("tokenAddress") or "",
                "chain": it.get("chainId") or "",
                "url": it.get("url") or "",
                "source": "dexscreener:token-boosts",
            }
        )
    if not tokens:
        return None
    return tokens, "dexscreener:token-boosts"


async def trending(*, chain: str = "solana", limit: int = 10) -> ToolResult:
    """Fetch real trending tokens (backend fused source first)."""
    tokens, source = (
        (await _fetch_market_trending(limit)) or (await _fetch_token_boosts(limit)) or (None, "")
    )
    if tokens is None:
        return ToolResult(
            tool="trending",
            ok=False,
            error="no live trending data (backend + DexScreener unavailable)",
        )

    return ToolResult(
        tool="trending",
        ok=True,
        data={"chain": chain, "limit": limit, "tokens": tokens},
        symbols=[t["symbol"] for t in tokens if t.get("symbol")],
        addresses=[t["address"] for t in tokens if t.get("address")],
        source=source,
    )


async def news_headlines(*, limit: int = 5) -> ToolResult:
    """Real top news headlines from our backend news feed."""
    body = await backend_json_get("/api/v1/news/trending", params={"limit": limit}, timeout=6.0)
    if not body:
        return ToolResult(
            tool="news_headlines",
            ok=False,
            error="no live news (backend /api/v1/news/trending unavailable)",
        )
    items = body.get("items") or body.get("articles") or []
    headlines = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        if title:
            headlines.append(
                {"title": title, "url": it.get("url") or "", "source": it.get("source") or ""}
            )
    if not headlines:
        return ToolResult(tool="news_headlines", ok=False, error="no headlines returned")
    return ToolResult(
        tool="news_headlines",
        ok=True,
        data={"headlines": headlines},
        symbols=[],
        addresses=[],
        source="backend:/api/v1/news/trending",
    )
