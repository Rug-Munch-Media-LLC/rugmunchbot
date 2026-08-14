"""market_overview tool + shared price fallback helpers.

``market_overview`` returns a one-call market picture:
  * global market cap / 24h volume / BTC-ETH dominance (CoinGecko /global),
  * BTC / ETH / SOL prices (backend /api/v1/databus/market/prices → CoinGecko),
  * Fear & Greed index (backend → alternative.me),
  * top movers (CoinGecko trending) and top chains by TVL (DefiLlama).

The price fallback helpers (``coingecko_quote`` / ``coinpaprika_quote`` /
``coincap_quote``) are the last layers of the ``price_lookup`` fallback chain
(backend → DexScreener → Birdeye → CoinGecko → CoinPaprika/CoinCap) — see
``tools/price.py`` which imports them.

All numbers come from live sources probed 2026-08-14 (see module notes).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import COINGECKO, COINGECKO_API_KEY
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_any, http_get_json
from rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.market")

COINPAPRIKA = "https://api.coinpaprika.com/v1"
COINCAP = "https://api.coincap.io/v2"
DEFILLAMA = "https://api.llama.fi/v2"
FEAR_GREED = "https://api.alternative.me/fng"


def _coingecko_headers() -> dict[str, str]:
    """CoinGecko demo-key header when the gopass/env key is present."""
    key = COINGECKO_API_KEY
    return {"x-cg-demo-api-key": key} if key else {}


# ── Price fallback helpers (last layers of the price_lookup chain) ─────────


async def coingecko_quote(symbol: str) -> dict[str, Any] | None:
    """CoinGecko price quote for a $SYMBOL (search → coin detail)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    search = await http_get_json(
        f"{COINGECKO}/search", params={"query": sym}, headers=_coingecko_headers(), timeout=6.0
    )
    coins = search.get("coins") if search else None
    if not isinstance(coins, list):
        return None
    match = next(
        (c for c in coins if isinstance(c, dict) and (c.get("symbol") or "").upper() == sym), None
    )
    if not match:
        return None
    coin_id = match.get("id")
    if not coin_id:
        return None
    detail = await http_get_json(
        f"{COINGECKO}/coins/{coin_id}",
        params={"localization": "false", "tickers": "false", "market_data": "true"},
        headers=_coingecko_headers(),
        timeout=6.0,
    )
    if not detail:
        return None
    raw_md = detail.get("market_data")
    md = raw_md if isinstance(raw_md, dict) else {}
    return {
        "symbol": sym,
        "name": detail.get("name") or match.get("name"),
        "price_usd": (md.get("current_price") or {}).get("usd"),
        "change_24h": md.get("price_change_percentage_24h"),
        "volume_24h": (md.get("total_volume") or {}).get("usd"),
        "market_cap": (md.get("market_cap") or {}).get("usd"),
        "source": "coingecko",
    }


async def coinpaprika_quote(symbol: str) -> dict[str, Any] | None:
    """CoinPaprika price quote for a $SYMBOL (search → ticker)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    search = await http_get_json(
        f"{COINPAPRIKA}/search", params={"q": sym, "c": "currencies"}, timeout=6.0
    )
    items = search if isinstance(search, list) else (search or {}).get("currencies")
    if not isinstance(items, list):
        return None
    match = next(
        (c for c in items if isinstance(c, dict) and (c.get("symbol") or "").upper() == sym), None
    )
    coin_id = (match or {}).get("id")
    if not coin_id:
        return None
    ticker = await http_get_json(f"{COINPAPRIKA}/tickers/{coin_id}", timeout=6.0)
    if not ticker:
        return None
    usd = ticker.get("quotes", {}).get("USD") if isinstance(ticker.get("quotes"), dict) else {}
    return {
        "symbol": sym,
        "name": ticker.get("name"),
        "price_usd": usd.get("price"),
        "change_24h": usd.get("percent_change_24h"),
        "volume_24h": usd.get("volume_24h"),
        "market_cap": usd.get("market_cap"),
        "source": "coinpaprika",
    }


async def coincap_quote(symbol: str) -> dict[str, Any] | None:
    """CoinCap price quote for a $SYMBOL (assets search)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    body = await http_get_json(f"{COINCAP}/assets", params={"search": sym.lower()}, timeout=6.0)
    assets = body.get("data") if body else None
    if not isinstance(assets, list):
        return None
    match = next(
        (a for a in assets if isinstance(a, dict) and (a.get("symbol") or "").upper() == sym), None
    )
    if not match:
        return None
    price = match.get("priceUsd")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    return {
        "symbol": sym,
        "name": match.get("name"),
        "price_usd": price,
        "change_24h": match.get("changePercent24Hr"),
        "volume_24h": match.get("volumeUsd24Hr"),
        "market_cap": match.get("marketCapUsd"),
        "source": "coincap",
    }


async def symbol_quote_fallback(symbol: str) -> dict[str, Any] | None:
    """Rotate through CoinGecko → CoinPaprika → CoinCap until one returns data."""
    for fetcher in (coingecko_quote, coinpaprika_quote, coincap_quote):
        try:
            quote = await fetcher(symbol)
        except (TimeoutError, OSError) as exc:
            logger.debug("symbol fallback %s failed: %s", fetcher.__name__, exc)
            quote = None
        if quote and quote.get("price_usd") is not None:
            return quote
    return None


# ── Market overview sources ────────────────────────────────────────────────


async def _backend_prices() -> dict[str, Any] | None:
    """BTC/ETH/SOL prices from our backend (CoinGecko front, cached)."""
    body = await backend_json_get(
        "/api/v1/databus/market/prices", params={"coins": "bitcoin,ethereum,solana"}, timeout=6.0
    )
    if not body or not isinstance(body.get("prices"), dict):
        return None
    return body["prices"]  # type: ignore[no-any-return]  # httpx/sqlite JSON Any access (ported verbatim)


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
    body = await http_get_json(
        f"{COINGECKO}/search/trending", headers=_coingecko_headers(), timeout=6.0
    )
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
        out.append(
            {"name": c.get("name"), "tvl": c.get("tvl"), "tokenSymbol": c.get("tokenSymbol")}
        )
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

    if (
        not payload.get("prices")
        and not payload.get("global_market_cap_usd")
        and not payload.get("fear_greed")
    ):
        return ToolResult(
            tool="market_overview",
            ok=False,
            error="no live market data (backend + CoinGecko + DefiLlama unavailable)",
        )

    return ToolResult(
        tool="market_overview",
        ok=True,
        data=payload,
        symbols=["BTC", "ETH", "SOL"] if prices else [],
        addresses=[],
        source="; ".join(sources),
    )
