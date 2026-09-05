"""Liquidation cascade predictor — where forced unwinds chain next.

Win 34. Consumes the multi-venue liquidation map (real OI + 10x/25x
reference levels) and scores each asset by how CLOSE price currently is
to its high-pressure liquidation bands:

    cascade_risk = OI_pressure x proximity_decay

Proximity decay: risk halves with every doubling of distance to the
nearest heavy liq level. Assets within 2% of a major band score highest —
that's where forced selling begets forced buying begets the cascade.

Output: ranked watchlist with side (long/short), distance, and the
price level that triggers the cascade.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugmunchbot.config._endpoints import backend_headers

logger = logging.getLogger(__name__)

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")


def _f(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


async def _fetch_liqmap(limit: int = 40) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/market/liquidations-multi-venue",
            params={"limit": limit},
            headers=backend_headers(),
        )
        if r.status_code != 200:
            raise RuntimeError(f"liqmap HTTP {r.status_code}")
        return r.json()


def _proximity_decay(distance_pct: float, half_life: float = 2.0) -> float:
    """Inverse-distance decay: 1.0 at 0%, 0.5 at half-life (2%), ~0.33 at 4%.

    Simple hyperbolic falloff — monotonic, no floor artifacts.
    """
    if distance_pct <= 0:
        return 1.0
    return round(1.0 / (1.0 + distance_pct / half_life), 4)


def analyze(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score cascade risk per asset from heatmap rows."""
    out: list[dict[str, Any]] = []
    for row in rows:
        mark = _f(row.get("weighted_mark"))
        oi = _f(row.get("total_oi_usd"))
        long10 = _f(row.get("long_liq_10x"))
        short10 = _f(row.get("short_liq_10x"))
        if not all(v is not None and v > 0 for v in (mark, oi, long10, short10)):
            continue
        assert mark and long10 and short10  # noqa: PT018 — guard, not test

        dist_long = abs(mark - long10) / mark * 100
        dist_short = abs(short10 - mark) / mark * 100
        near_side = "LONGS" if dist_long <= dist_short else "SHORTS"
        near_dist = min(dist_long, dist_short)

        decay = _proximity_decay(near_dist)
        _pressure = oi * decay
        risk_score = round(min(100.0, decay * 60 + min(oi / 1e9, 1.0) * 40), 1)

        band = "IMMINENT" if near_dist < 1 else "DANGER" if near_dist < 3 else "WATCH" if near_dist < 6 else "CLEAR"
        out.append({
            "asset": row["asset"],
            "cascade_risk": risk_score,
            "band": band,
            "near_side": near_side,
            "distance_pct": round(near_dist, 2),
            "trigger_price_10x": long10 if near_side == "LONGS" else short10,
            "total_oi_usd": row["total_oi_usd"],
            "venue_count": row.get("venue_count"),
            "read": (
                f"{row['asset']}: {near_side.title()} liquidations {near_dist:.1f}% away "
                f"at {long10 if near_side == 'LONGS' else short10} on {_usd(oi)} OI — "
                f"{band.lower()}"
            ),
        })
    out.sort(key=lambda x: x["cascade_risk"], reverse=True)
    return out


def _usd(v: float) -> str:
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


async def cascade_watch(*, limit: int = 10) -> dict[str, Any]:
    """Ranked cascade-risk watchlist from live multi-venue data."""
    try:
        body = await _fetch_liqmap(limit=40)
    except Exception as exc:
        return {"error": f"liquidation map unavailable: {exc!r}"[:140]}
    rows = body.get("heatmap") or []
    if not rows:
        return {"error": "no liquidation data"}
    ranked = analyze(rows)[:limit]
    imminent = [r for r in ranked if r["band"] in ("IMMINENT", "DANGER")]
    from rugmunchbot.tools.registry import ToolResult

    return ToolResult(
        tool="cascade_watch",
        ok=True,
        data={
            "watchlist": ranked,
            "imminent_count": len(imminent),
            "venues_online": body.get("venues_online"),
            "total_oi_usd": body.get("total_open_interest_usd"),
            "headline": (
                f"{len(imminent)} assets in cascade-danger zone"
                if imminent
                else "no assets within cascade-danger distance"
            ),
        },
    )
