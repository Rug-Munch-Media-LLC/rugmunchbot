"""prediction_markets tool — Polymarket (+ Kalshi when reachable).

Data sources (reuse-first, honest degradation):

  1. OUR backend ``/api/v1/market/predictions`` — reuses the existing
     ``_polymarket`` provider in ``app/routers/market_intel/providers.py``
     (Gamma API, backend-cached). No new Polymarket client.
  2. Polymarket Gamma API directly (``https://gamma-api.polymarket.com``) —
     same query pattern (``active=true, closed=false, order=volume24hr``) as
     the existing client in
     ``polymarket-insider-tracker/src/polymarket_insider_tracker/ingestor/gamma_client.py``;
     used for enrichment (24h/1w volume, 1w/1m price change, resolution
     status) and as fallback when the backend route is down.
  3. Kalshi public trade-api (``api.elections.kalshi.com``) — secondary
     venue, no auth, noted when reachable.

Returned per market: title, current YES/NO odds, volume, volume_24h,
liquidity, price-change fields the source actually exposes, resolution
status and resolves-at.

24h price change: neither the backend provider nor the Gamma markets
payload exposes a 24h price delta (Gamma exposes ``oneWeekPriceChange`` /
``oneMonthPriceChange``). We return those real fields and set
``change_24h`` to null rather than inventing a number.

Never fabricate: if every source is unreachable the tool returns
``ok=False`` with the honest "no live data" message.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_any
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.prediction_markets")

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
KALSHI_MARKETS = "https://api.elections.kalshi.com/trade-api/v2/markets"

# Real field names returned by Gamma markets payload (verified by probe).
_GAMMA_CHANGE_FIELDS = ("oneWeekPriceChange", "oneMonthPriceChange")


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_price(outcome_prices: Any) -> float | None:
    """YES-side price from Gamma's outcomePrices string "['0.45','0.55']"."""
    if isinstance(outcome_prices, str):
        try:
            parsed = json.loads(outcome_prices)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list) and parsed:
            return _to_float(parsed[0])
    if isinstance(outcome_prices, list) and outcome_prices:
        return _to_float(outcome_prices[0])
    return None


def _resolution_status(closed: bool | None, end_date: str | None) -> str:
    """Real resolution status from market state + end date."""
    if closed:
        return "resolved"
    if end_date:
        try:
            end = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if (end - datetime.now(UTC)).total_seconds() <= 86400:
                return "closing_soon"
        except (ValueError, TypeError) as e:
            logger.debug("prediction market parse failed: %s", e)
    return "open"


# ── Backend route (reuses our _polymarket provider) ───────────────────────


async def _backend_predictions(limit: int) -> list[dict[str, Any]] | None:
    """Our backend /api/v1/market/predictions — reuses the existing
    Polymarket provider (Gamma, backend-cached)."""
    body = await backend_json_get("/api/v1/market/predictions", params={"limit": limit}, timeout=8.0)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not items:
        return None
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "venue": "polymarket",
                "id": it.get("id") or "",
                "title": title,
                "yes_price": _to_float(it.get("yes_price")),
                "no_price": _to_float(it.get("no_price")),
                "volume": _to_float(it.get("volume")),
                "volume_24h": None,
                "volume_1w": None,
                "liquidity": _to_float(it.get("liquidity")),
                "price_change_1w": None,
                "price_change_1m": None,
                "change_24h": None,
                "resolution_status": _resolution_status(None, it.get("resolution_date")),
                "resolves_at": it.get("resolution_date") or "",
                "category": it.get("category") or "",
                "source": "backend:/api/v1/market/predictions (reuses our _polymarket provider)",
            }
        )
    return out or None


# ── Gamma API (reuses polymarket-insider-tracker gamma_client pattern) ────


async def _gamma_markets(limit: int) -> list[dict[str, Any]] | None:
    """Top active Polymarket markets by 24h volume (volume24hr order).

    Same query shape as polymarket-insider-tracker's GammaClient
    (``get_active_market_stats``): active=true, closed=false,
    order=volume24hr, ascending=false.
    """
    body = await http_get_any(
        GAMMA_MARKETS,
        params={
            "limit": limit,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
        },
        timeout=10.0,
    )
    if not isinstance(body, list) or not body:
        return None
    out: list[dict[str, Any]] = []
    for m in body:
        if not isinstance(m, dict):
            continue
        question = (m.get("question") or "").strip()
        if not question:
            continue
        yes = _first_price(m.get("outcomePrices"))
        no = (1.0 - yes) if yes is not None else None
        slug = str(m.get("slug") or "")
        out.append(
            {
                "venue": "polymarket",
                "id": m.get("conditionId") or m.get("id") or "",
                "title": question,
                "yes_price": yes,
                "no_price": no,
                "volume": _to_float(m.get("volumeNum") or m.get("volume")),
                "volume_24h": _to_float(m.get("volume24hr")),
                "volume_1w": _to_float(m.get("volume1wk")),
                "liquidity": _to_float(m.get("liquidityNum") or m.get("liquidity")),
                "price_change_1w": _to_float(m.get("oneWeekPriceChange")),
                "price_change_1m": _to_float(m.get("oneMonthPriceChange")),
                "change_24h": None,  # not exposed by the source — honest null
                "resolution_status": _resolution_status(bool(m.get("closed")), m.get("endDate")),
                "resolves_at": m.get("endDate") or "",
                "category": (m.get("events") or [{}])[0].get("category")
                if isinstance(m.get("events"), list) and m.get("events")
                else None,
                "url": f"https://polymarket.com/event/{slug}" if slug else "",
                "source": "gamma-api.polymarket.com/markets (reuses insider-tracker GammaClient pattern)",
            }
        )
    return out or None


# ── Kalshi (secondary venue, public API) ──────────────────────────────────


async def _kalshi_markets(limit: int) -> list[dict[str, Any]] | None:
    """Top open Kalshi markets (public trade-api v2, no auth).

    The v2 payload is dollar-denominated (``yes_bid_dollars``,
    ``yes_ask_dollars``, ``last_price_dollars``, ``volume_fp``,
    ``volume_24h_fp``, ``liquidity_dollars``) — verified by probe. When no
    last price exists we fall back to the mid of yes bid/ask.
    """
    body = await http_get_any(
        KALSHI_MARKETS,
        params={"limit": limit, "status": "open"},
        timeout=8.0,
    )
    markets = body.get("markets") if isinstance(body, dict) else None
    if not isinstance(markets, list) or not markets:
        return None
    out: list[dict[str, Any]] = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        title = (m.get("title") or "").strip()
        if not title:
            continue
        yes = _to_float(m.get("last_price_dollars"))
        if yes is None:
            bid = _to_float(m.get("yes_bid_dollars"))
            ask = _to_float(m.get("yes_ask_dollars"))
            if bid is not None and ask is not None:
                yes = (bid + ask) / 2.0
            elif bid is not None:
                yes = bid
            elif ask is not None:
                yes = ask
        out.append(
            {
                "venue": "kalshi",
                "id": m.get("ticker") or "",
                "title": title,
                "yes_price": yes,
                "no_price": (1.0 - yes) if yes is not None else None,
                "volume": _to_float(m.get("volume_fp") or m.get("volume")),
                "volume_24h": _to_float(m.get("volume_24h_fp")),
                "volume_1w": None,
                "liquidity": _to_float(m.get("liquidity_dollars")),
                "price_change_1w": None,
                "price_change_1m": None,
                "change_24h": None,  # not exposed by Kalshi — honest null
                "resolution_status": _resolution_status(bool(m.get("status") == "closed"), m.get("close_time")),
                "resolves_at": m.get("close_time") or "",
                "category": (m.get("category") or "")[:40],
                "source": "kalshi:trade-api/v2/markets (public, no auth)",
            }
        )
    return out or None


# ── Tool handler ──────────────────────────────────────────────────────────


async def prediction_markets(*, limit: int = 8, venue: str = "all") -> ToolResult:
    """Top prediction markets with current odds, volume and resolution.

    Args:
        limit: max markets to return (per venue when venue != all).
        venue: "polymarket", "kalshi", or "all".

    Returns:
        ToolResult with ``markets`` and ``venues`` (which sources were
        reachable). ``ok=False`` only when every venue is unreachable.
    """
    try:
        limit_i = max(1, min(int(limit), 30))
    except (TypeError, ValueError):
        limit_i = 8
    venue_key = venue.strip().lower()

    markets: list[dict[str, Any]] = []
    venues: dict[str, bool] = {}

    if venue_key in ("all", "polymarket"):
        # Backend provider first (reuse), Gamma for enrichment/fallback.
        backend = await _backend_predictions(limit_i)
        gamma = await _gamma_markets(limit_i)
        if gamma:
            venues["polymarket"] = True
            markets.extend(gamma[:limit_i])
        elif backend:
            venues["polymarket"] = True
            markets.extend(backend[:limit_i])
        else:
            venues["polymarket"] = False

    if venue_key in ("all", "kalshi"):
        kalshi = await _kalshi_markets(limit_i)
        if kalshi:
            venues["kalshi"] = True
            markets.extend(kalshi[:limit_i])
        else:
            venues["kalshi"] = False

    if not markets:
        return ToolResult(
            tool="prediction_markets",
            ok=False,
            error="no live prediction-market data (backend provider, Gamma and Kalshi all unreachable)",
        )

    return ToolResult(
        tool="prediction_markets",
        ok=True,
        data={
            "limit": limit_i,
            "count": len(markets),
            "markets": markets,
            "venues": venues,
            "note": "change_24h is null because no source exposes a 24h price delta; "
            "Polymarket Gamma provides oneWeekPriceChange/oneMonthPriceChange instead.",
            "updated_at": datetime.now(UTC).isoformat(),
        },
        symbols=[],
        addresses=[],
        source=", ".join(k for k, ok in venues.items() if ok) if any(venues.values()) else "no-live-source",
    )


# ── Registration (self-owned; invoked by tools/__init__._register_defaults) ─


def register_tool(reg: Any) -> None:
    """Register this tool on the shared registry (see trend_algo for notes)."""
    reg.register("prediction_markets", prediction_markets, timeout=12.0, ttl=300.0)
