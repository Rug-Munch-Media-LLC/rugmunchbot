"""Proactive funding-dislocation alerts (win 32).

Polls the multi-venue aggregator; posts EXTREME cross-venue funding
dislocations to the alpha channel. Dedupe by asset so the same signal
never double-posts. Fail-open: any error skips the tick silently (logged).

Copy doctrine (re-authored 2026-10-17): the old "⚡ Funding Dislocation
Alert" header + "via @rugmunchbot · rugmunch.io" closer was generic
template filler. The new copy leads with the trade the dislocation implies
(which venue pays, which venue charges, the spread), names the number, and
closes with the specific read — no "here is a signal", no boilerplate
footer. The graphic comes from ``POST_TYPE_GRAPHICS`` (operator originals).
"""

from __future__ import annotations

import html
import logging
import os
import time
from typing import Any

from rugmunchbot.channels.formatters._primitives import NewsPost
from rugmunchbot.support.post_graphics import graphic_for

logger = logging.getLogger(__name__)

_SEVERITY_MIN = os.getenv("DISLOCATION_ALERT_MIN", "EXTREME")
_DEDUPE_TTL = float(os.getenv("DISLOCATION_DEDUPE_TTL", "21600"))  # 6h
_seen: dict[str, float] = {}

#: Masthead — names the signal and the desk, not a generic "Alert" banner.
_DISLO_HEADER = "⚡ <b>FUNDING DISLOCATION</b>"
_DISLO_SUB = "<i>Same asset, two venues, two prices for leverage — the spread is the trade.</i>"


def _dedupe(asset: str) -> bool:
    """True first time within TTL, False after. Prunes lazily."""
    now = time.time()
    for k in [k for k, t in _seen.items() if now - t > _DEDUPE_TTL]:
        _seen.pop(k, None)
    if asset in _seen:
        return False
    _seen[asset] = now
    return True


async def build_dislocation_post() -> NewsPost | None:
    """Render the alert, or None when nothing worth posting.

    Returns a ``NewsPost`` carrying the dislocation brand graphic so the
    scheduler attaches it as a photo (image doctrine: every post carries an
    image). Callers may treat it as a plain ``str`` (``NewsPost`` subclasses
    ``str``).
    """
    from rugmunchbot.tools.funding_dislocation import funding_dislocation

    result = await funding_dislocation(limit=10)
    d = result.data or {}
    signals = [
        s
        for s in (d.get("signals") or [])
        if s.get("severity") == _SEVERITY_MIN
        or (_SEVERITY_MIN == "NOTABLE" and s.get("severity") in ("NOTABLE", "EXTREME"))
    ]
    fresh = [s for s in signals if _dedupe(str(s.get("asset")))]
    if not fresh:
        return None

    lines = [_DISLO_HEADER, _DISLO_SUB, ""]
    for s in fresh[:5]:
        emoji = "\U0001f6a8" if s["severity"] == "EXTREME" else "\u26a1"
        rich, cheap = s["richest_funding"], s["cheapest_funding"]
        spread = (rich["rate"] - cheap["rate"]) * 100
        lines.append(
            f"{emoji} <b>{html.escape(str(s['asset']))}</b> — "
            f"long {html.escape(str(cheap['venue']))} "
            f"{cheap['rate'] * 100:+.4f}%/h, short "
            f"{html.escape(str(rich['venue']))} {rich['rate'] * 100:+.4f}%/h "
            f"(<b>{spread:+.4f}%/h spread</b>)"
        )
        read = _e_read(s)
        if read:
            lines.append(f"   {read}")
    lines.append("")
    lines.append("Funding this wide rarely holds — it closes when one side capitulates.")
    post = NewsPost("\n".join(lines))
    post.image_url = graphic_for("dislocation")
    return post


def _e_read(s: dict[str, Any]) -> str:
    return html.escape(str(s.get("read") or ""), quote=False)


__all__ = ["build_dislocation_post"]
