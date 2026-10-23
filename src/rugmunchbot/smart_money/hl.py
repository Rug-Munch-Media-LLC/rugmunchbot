"""Hyperliquid perp snapshot: movers, funding squeezes, liquidation clusters.

Both endpoints are market-level: asset, size, funding, OI. They carry **no
wallet identity and no liquidation attribution** — a post built from this
feed must never name a trader.
"""

from __future__ import annotations

from typing import Any

from ._http import backend_json
from .types import Feed

_ACTION = "/api/v1/market/hyperliquid-action"
_LIQS = "/api/v1/market/hyperliquid-liquidations"


def _mover(row: dict[str, Any], direction: str) -> dict[str, Any]:
    return {
        "kind": "mover",
        "direction": direction,
        "asset": row.get("symbol") or row.get("asset"),
        "price": row.get("price"),
        "change_24h_pct": row.get("change_24h"),
        "volume_24h": row.get("volume_24h"),
        "open_interest_usd": row.get("open_interest_usd") or row.get("open_interest"),
        "funding_rate": row.get("funding_rate"),
    }


def _squeeze(row: dict[str, Any], direction: str) -> dict[str, Any]:
    return {
        "kind": "squeeze",
        "direction": direction,
        "asset": row.get("asset") or row.get("symbol"),
        "funding_rate": row.get("funding_rate"),
        "open_interest_usd": row.get("open_interest_usd") or row.get("open_interest"),
    }


def _cluster(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "cluster",
        "asset": row.get("asset"),
        "mark_price": row.get("mark_price"),
        "open_interest_usd": row.get("open_interest_usd"),
        "funding_rate": row.get("funding_rate"),
        "liquidation_pressure": row.get("liquidation_pressure"),
        "long_liq_price_10x": row.get("long_liq_price_10x"),
        "short_liq_price_10x": row.get("short_liq_price_10x"),
        "oi_share_pct": row.get("oi_share_pct"),
    }


async def fetch_hl(limit: int = 6) -> Feed:
    """Fetch the Hyperliquid snapshot (action feed + liquidation clusters)."""
    tried = ["market/hyperliquid-action", "market/hyperliquid-liquidations"]
    action = await backend_json(_ACTION, params={"limit": limit}) or {}
    liqs = await backend_json(_LIQS, params={"limit": max(limit, 5)}) or {}

    items: list[dict[str, Any]] = []
    for row in (action.get("top_gainers") or [])[:3]:
        items.append(_mover(row, "up"))
    for row in (action.get("top_losers") or [])[:3]:
        items.append(_mover(row, "down"))
    for row in (action.get("long_squeeze_highest_funding") or [])[:1]:
        items.append(_squeeze(row, "long_squeeze"))
    for row in (action.get("short_squeeze_lowest_funding") or [])[:1]:
        items.append(_squeeze(row, "short_squeeze"))
    for row in (liqs.get("heatmap") or [])[:3]:
        items.append(_cluster(row))

    if not items:
        return Feed(kind="hl", note="Hyperliquid snapshot unavailable", tried=tuple(tried))
    updated = action.get("updated_at") or liqs.get("updated_at") or ""
    return Feed(
        kind="hl",
        items=tuple(items),
        source="hyperliquid.metaAndAssetCtxs",
        updated_at=str(updated),
        tried=tuple(tried),
    )
