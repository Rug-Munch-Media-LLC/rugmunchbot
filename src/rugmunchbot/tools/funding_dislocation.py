"""Cross-venue funding dislocation detector — PREMIUM intelligence.

Only possible because we aggregate funding from 7 venues in one place.
When the SAME asset pays wildly different funding on different exchanges,
one of two things is true:

  1. A venue's perp is dislocated from spot (price basis) -> cash-and-carry
     or squeeze setup.
  2. Crowding is split -- longs pile up on one venue, shorts on another ->
     whichever side capitulates moves the asset on BOTH.

Signal = funding spread (max - min across venues) + OI weight. This is the
trade desk's cross-venue basis monitor, free, for every degen.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugmunchbot.config._endpoints import backend_headers
from rugmunchbot.tools.registry import ToolResult

logger = logging.getLogger(__name__)

# Spread thresholds (funding is per-hour on most venues; 8h-equivalent shown).
_SPREAD_NOTABLE = 0.0005  # 0.05%/h
_SPREAD_EXTREME = 0.002  # 0.2%/h


async def funding_dislocation(*, limit: int = 10, min_oi_usd: float = 1e6) -> ToolResult:
    """ToolResult: assets ranked by cross-venue funding spread."""
    backend = os.getenv("BACKEND_URL", "http://localhost:8000")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{backend}/api/v1/market/liquidations-multi-venue",
                params={"limit": 50},
                headers=backend_headers(),
            )
            body = r.json() if r.status_code == 200 else {}
    except Exception as exc:
        return ToolResult(tool="funding_dislocation", ok=False,
                          data={"error": f"multi-venue feed unavailable: {exc!r}"[:160]})

    rows = (body or {}).get("heatmap") or []
    signals: list[dict[str, Any]] = []
    for row in rows:
        if float(row.get("total_oi_usd") or 0) < min_oi_usd:
            continue
        venues = row.get("venues") or {}
        funds = {
            v: d.get("funding")
            for v, d in venues.items()
            if isinstance(d.get("funding"), (int, float))
        }
        if len(funds) < 2:
            continue
        vals = list(funds.values())
        spread = max(vals) - min(vals)
        if spread < _SPREAD_NOTABLE:
            continue
        hi_venue = max(funds, key=lambda k: funds[k])
        lo_venue = min(funds, key=lambda k: funds[k])
        severity = "EXTREME" if spread >= _SPREAD_EXTREME else "NOTABLE"
        signals.append({
            "asset": row.get("asset"),
            "total_oi_usd": row.get("total_oi_usd"),
            "spread_per_hour": round(spread, 6),
            "severity": severity,
            "richest_funding": {"venue": hi_venue, "rate": round(funds[hi_venue], 6)},
            "cheapest_funding": {"venue": lo_venue, "rate": round(funds[lo_venue], 6)},
            "read": (
                f"{row.get('asset')}: pays {funds[hi_venue] * 100:+.4f}%/h on "
                f"{hi_venue} vs {funds[lo_venue] * 100:+.4f}%/h on {lo_venue} — "
                + ("crowding split; squeeze fuel on one side." if severity == "EXTREME"
                   else "basis worth watching.")
            ),
        })

    signals.sort(key=lambda x: x["spread_per_hour"], reverse=True)
    return ToolResult(
        tool="funding_dislocation",
        ok=True,
        data={
            "signals": signals[:limit],
            "scanned_assets": len(rows),
            "flagged": len(signals),
            "thresholds": {"notable_per_hour": _SPREAD_NOTABLE, "extreme_per_hour": _SPREAD_EXTREME},
        },
    )
