"""Proactive funding-dislocation alerts (win 32).

Polls the multi-venue aggregator; posts EXTREME dislocations to the
alerts channel. Dedupe by asset so the same signal never double-posts.
Fail-open: any error skips the tick silently (logged).
"""

from __future__ import annotations

import html
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_SEVERITY_MIN = os.getenv("DISLOCATION_ALERT_MIN", "EXTREME")
_DEDUPE_TTL = float(os.getenv("DISLOCATION_DEDUPE_TTL", "21600"))  # 6h
_seen: dict[str, float] = {}

_DISLO_HEADER = "\u26a1 <b>Funding Dislocation Alert</b>"


def _dedupe(asset: str) -> bool:
    """True first time within TTL, False after. Prunes lazily."""
    now = time.time()
    for k in [k for k, t in _seen.items() if now - t > _DEDUPE_TTL]:
        _seen.pop(k, None)
    if asset in _seen:
        return False
    _seen[asset] = now
    return True


async def build_dislocation_post() -> str | None:
    """Render the alert text, or None when nothing worth posting."""
    from rugmunchbot.tools.funding_dislocation import funding_dislocation

    result = await funding_dislocation(limit=10)
    d = result.data or {}
    signals = [
        s for s in (d.get("signals") or [])
        if s.get("severity") == _SEVERITY_MIN
        or (_SEVERITY_MIN == "NOTABLE" and s.get("severity") in ("NOTABLE", "EXTREME"))
    ]
    fresh = [s for s in signals if _dedupe(str(s.get("asset")))]
    if not fresh:
        return None

    lines = [_DISLO_HEADER, ""]
    for s in fresh[:5]:
        emoji = "\U0001f6a8" if s["severity"] == "EXTREME" else "\u26a1"
        rich, cheap = s["richest_funding"], s["cheapest_funding"]
        lines.append(
            f"{emoji} <b>{html.escape(str(s['asset']))}</b> \u2014 "
            f"{html.escape(str(rich['venue']))} {rich['rate'] * 100:+.4f}%/h vs "
            f"{html.escape(str(cheap['venue']))} {cheap['rate'] * 100:+.4f}%/h"
        )
        lines.append(f"   {_e_read(s)}")
    lines.append("")
    lines.append("via @rugmunchbot \u00b7 rugmunch.io")
    return "\n".join(lines)


def _e_read(s: dict[str, Any]) -> str:
    return html.escape(str(s.get("read") or ""), quote=False)
