"""Proactive social-velocity spike alerts (win 36).

Watches mention velocity across watched symbols; posts when chatter
spikes hard (early narrative detection). Dedupes per symbol; fail-open.

Copy doctrine (re-authored 2026-10-17): the old "📡 Social Velocity Spike"
header + "via @rugmunchbot · rugmunch.io" closer was generic template
filler. The new copy leads with the number (mentions/24h), names the
direction and the lean, and closes with the specific read — chatter with
no price move is either a pre-pump or a shill ramp, and we say which.
No boilerplate footer. The graphic comes from ``POST_TYPE_GRAPHICS``.
"""

from __future__ import annotations

import html
import logging
import os
import time

from rugmunchbot.channels.formatters._primitives import NewsPost
from rugmunchbot.support.post_graphics import graphic_for

logger = logging.getLogger(__name__)

_SPIKE_MULTIPLIER = float(os.getenv("SOCIAL_SPIKE_MULTIPLIER", "3.0"))
_MIN_MENTIONS = int(os.getenv("SOCIAL_SPIKE_MIN_MENTIONS", "30"))
_DEDUPE_TTL = float(os.getenv("SOCIAL_SPIKE_DEDUPE_TTL", "14400"))  # 4h
_seen: dict[str, float] = {}

_WATCHLIST = [
    s.strip().upper() for s in os.getenv("SOCIAL_WATCHLIST", "BTC,ETH,SOL").split(",") if s.strip()
]

#: Masthead — names the signal and the desk, not a generic "Spike" banner.
_SPIKE_HEADER = "📡 <b>CHATTER SPIKE</b>"
_SPIKE_SUB = "<i>Mentions moving before price is either an early narrative or a shill ramp.</i>"


def _dedupe(symbol: str) -> bool:
    now = time.time()
    for k in [k for k, t in _seen.items() if now - t > _DEDUPE_TTL]:
        _seen.pop(k, None)
    if symbol in _seen:
        return False
    _seen[symbol] = now
    return True


async def build_social_spike_post() -> NewsPost | None:
    """Render spike alert text, or None when nothing qualifies.

    Returns a ``NewsPost`` carrying the social-spike brand graphic so the
    scheduler attaches it as a photo (image doctrine: every post carries an
    image). Callers may treat it as a plain ``str`` (``NewsPost`` subclasses
    ``str``).
    """
    from rugmunchbot.tools.social_intel import social_intel

    lines: list[str] = []
    for sym in _WATCHLIST[:5]:
        result = await social_intel(symbol=sym)
        d = result.data or {}
        if not result.ok:
            continue
        mentions = int(d.get("mentions_24h") or 0)
        velocity = str(d.get("mention_velocity") or "")
        # Spike = high velocity + strong lean (directional narrative forming)
        lean = d.get("sentiment_lean")
        if (
            velocity == "high"
            and mentions >= _MIN_MENTIONS
            and lean is not None
            and abs(lean) >= 20
        ):
            if not _dedupe(sym):
                continue
            direction = "bullish" if lean > 0 else "bearish"
            emoji = "📈" if lean > 0 else "📉"
            lines.append(
                f"{emoji} <b>{html.escape(sym)}</b> — {mentions} mentions/24h, "
                f"sentiment {lean:+.0f} ({direction}). "
                f"Chatter is running hot; watch the tape for confirmation."
            )
    if not lines:
        return None
    out = [_SPIKE_HEADER, _SPIKE_SUB, ""]
    out.extend(lines)
    out.append("")
    out.append("Velocity without price is the tell — confirm before you chase.")
    post = NewsPost("\n".join(out))
    post.image_url = graphic_for("social_spike")
    return post


__all__ = ["build_social_spike_post"]
