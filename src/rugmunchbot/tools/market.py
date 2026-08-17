"""market_overview tool — one-call market picture (global + BTC/ETH/SOL + F&G).

``market_overview`` returns a one-call market picture:
  * global market cap / 24h volume / BTC-ETH dominance (CoinGecko /global),
  * BTC / ETH / SOL prices (backend /api/v1/databus/market/prices → CoinGecko),
  * Fear & Greed index (backend → alternative.me),
  * top movers (CoinGecko trending) and top chains by TVL (DefiLlama).

The price fallback helpers (``coingecko_quote`` / ``coinpaprika_quote`` /
``coincap_quote`` / ``symbol_quote_fallback``) moved to the shared pricing
service (``app/domains/telegram/rugmunchbot/services/pricing.py``) — they are
re-exported here as thin shims for import compatibility (nothing else should
grow its own fallback chain; call ``services.pricing.get_price`` instead).

All numbers come from live sources probed 2026-08-14 (see module notes).
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import COINGECKO
from app.domains.telegram.rugmunchbot.services.pricing import (
    _coingecko_headers,
    coincap_quote,
    coingecko_quote,
    coinpaprika_quote,
    symbol_quote_fallback,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_any, http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.market")

DEFILLAMA = "https://api.llama.fi/v2"
FEAR_GREED = "https://api.alternative.me/fng"


# ── Market overview sources ────────────────────────────────────────────────


async def _backend_prices() -> dict[str, Any] | None:
    """BTC/ETH/SOL prices from our backend (CoinGecko front, cached)."""
    body = await backend_json_get(
        "/api/v1/databus/market/prices", params={"coins": "bitcoin,ethereum,solana"}, timeout=6.0
    )
    if not body or not isinstance(body.get("prices"), dict):
        return None
    return body["prices"]


async def _backend_fear_greed() -> dict[str, Any] | None:
    body = await backend_json_get("/api/v1/databus/market/fear-greed", timeout=6.0)
    if not body or body.get("value") is None:
        return None
    return body


async def _direct_fear_greed() -> dict[str, Any] | None:
    """Fallback F&G directly from alternative.me (free, no key)."""
    body = await http_get_json(FEAR_GREED, params={"limit": 1}, timeout=6.0)
    data = body.get("data") if body else None
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    return {
        "value": row.get("value"),
        "classification": row.get("value_classification"),
        "source": "alternative.me",
    }


async def _coingecko_global() -> dict[str, Any] | None:
    body = await http_get_json(f"{COINGECKO}/global", headers=_coingecko_headers(), timeout=6.0)
    data = body.get("data") if body else None
    if not isinstance(data, dict):
        return None
    return data


async def _coingecko_trending(limit: int = 10) -> list[dict[str, Any]] | None:
    body = await http_get_json(f"{COINGECKO}/search/trending", headers=_coingecko_headers(), timeout=6.0)
    coins = body.get("coins") if body else None
    if not isinstance(coins, list):
        return None
    out: list[dict[str, Any]] = []
    for c in coins[:limit]:
        item = c.get("item") if isinstance(c, dict) else None
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "name": item.get("name"),
                "symbol": item.get("symbol"),
                "market_cap_rank": item.get("market_cap_rank"),
                "score": item.get("score"),
            }
        )
    return out or None


async def _defillama_top_chains(limit: int = 8) -> list[dict[str, Any]] | None:
    """Top chains by TVL from DefiLlama (chain-level market context).

    Uses the shared budget-gated :func:`_client.http_get_any` because
    DefiLlama ``/v2/chains`` returns an ARRAY — ``http_get_json`` is
    dict-only and would always degrade to ``None``. Mirrors
    ``market_condition._defillama_top_chains`` (both call the same shared
    helper; the local duplicate in market_condition.py predates it).
    """
    chains = await http_get_any(f"{DEFILLAMA}/chains", timeout=8.0)
    if not isinstance(chains, list):
        return None
    ranked = sorted(
        (c for c in chains if isinstance(c, dict)),
        key=lambda c: c.get("tvl") or 0,
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for c in ranked[:limit]:
        out.append({"name": c.get("name"), "tvl": c.get("tvl"), "tokenSymbol": c.get("tokenSymbol")})
    return out or None


# ── The tool ───────────────────────────────────────────────────────────────


async def market_overview(*, chain: str = "", limit: int = 10) -> ToolResult:
    """One-call market overview: global mcap, BTC/ETH/SOL, Fear&Greed, movers.

    Sources (probed 2026-08-14):
      * backend /api/v1/databus/market/prices + /market/fear-greed (own data first),
      * CoinGecko /global + /search/trending (free + optional demo key),
      * DefiLlama /v2/chains (top chains by TVL),
      * alternative.me direct F&G fallback.

    Returns ok=False only when every source is unreachable.
    """
    payload: dict[str, Any] = {"chain": chain or "global"}

    prices = await _backend_prices()
    fear = await _backend_fear_greed() or await _direct_fear_greed()
    global_data = await _coingecko_global()
    trending = await _coingecko_trending(limit)
    chains = await _defillama_top_chains(8)

    if prices:
        payload["prices"] = prices
    if fear:
        payload["fear_greed"] = fear
    if global_data:
        payload["global_market_cap_usd"] = (global_data.get("total_market_cap") or {}).get("usd")
        payload["global_volume_24h_usd"] = (global_data.get("total_volume") or {}).get("usd")
        payload["btc_dominance"] = (global_data.get("market_cap_percentage") or {}).get("btc")
        payload["eth_dominance"] = (global_data.get("market_cap_percentage") or {}).get("eth")
        payload["active_cryptocurrencies"] = global_data.get("active_cryptocurrencies")
    if trending:
        payload["trending_movers"] = trending
    if chains:
        payload["top_chains_tvl"] = chains

    sources: list[str] = []
    if prices:
        sources.append("backend:/api/v1/databus/market/prices")
    if fear:
        sources.append(f"{fear.get('source', 'fear_greed')}")
    if global_data:
        sources.append("coingecko:/global")
    if trending:
        sources.append("coingecko:/search/trending")
    if chains:
        sources.append("defillama:/v2/chains")

    if not payload.get("prices") and not payload.get("global_market_cap_usd") and not payload.get("fear_greed"):
        return ToolResult(
            tool="market_overview", ok=False, error="no live market data (backend + CoinGecko + DefiLlama unavailable)"
        )

    return ToolResult(
        tool="market_overview",
        ok=True,
        data=payload,
        symbols=["BTC", "ETH", "SOL"] if prices else [],
        addresses=[],
        source="; ".join(sources),
    )


# Re-exported shims — the price fallback helpers now live in the shared
# pricing service (``services/pricing.py``). These names stay importable from
# ``tools.market`` for compatibility with older imports (e.g.
# ``market_condition`` imports ``_coingecko_headers`` here).
__all__ = [
    "_coingecko_headers",
    "coincap_quote",
    "coingecko_quote",
    "coinpaprika_quote",
    "market_overview",
    "symbol_quote_fallback",
]
