"""Proactive social-velocity spike alerts (win 36).

Watches mention velocity across watched symbols; posts when chatter
spikes hard (early narrative detection). Dedupes per symbol; fail-open.
"""

from __future__ import annotations

import html
import logging
import os
import time

logger = logging.getLogger(__name__)

_SPIKE_MULTIPLIER = float(os.getenv("SOCIAL_SPIKE_MULTIPLIER", "3.0"))
_MIN_MENTIONS = int(os.getenv("SOCIAL_SPIKE_MIN_MENTIONS", "30"))
_DEDUPE_TTL = float(os.getenv("SOCIAL_SPIKE_DEDUPE_TTL", "14400"))  # 4h
_seen: dict[str, float] = {}

_WATCHLIST = [s.strip().upper() for s in os.getenv(
    "SOCIAL_WATCHLIST", "BTC,ETH,SOL"
).split(",") if s.strip()]


def _dedupe(symbol: str) -> bool:
    now = time.time()
    for k in [k for k, t in _seen.items() if now - t > _DEDUPE_TTL]:
        _seen.pop(k, None)
    if symbol in _seen:
        return False
    _seen[symbol] = now
    return True


async def build_social_spike_post() -> str | None:
    """Render spike alert text, or None when nothing qualifies."""
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
        if velocity == "high" and mentions >= _MIN_MENTIONS and lean is not None and abs(lean) >= 20:
            if not _dedupe(sym):
                continue
            direction = "bullish" if lean > 0 else "bearish"
            emoji = "📈" if lean > 0 else "📉"
            lines.append(
                f"{emoji} <b>{html.escape(sym)}</b> — {mentions} mentions/24h "
                f"({html.escape(velocity)}), sentiment {lean:+.0f} ({direction})"
            )
    if not lines:
        return None
    out = ["📡 <b>Social Velocity Spike</b>", ""]
    out.extend(lines)
    out.append("")
    out.append("via @rugmunchbot · rugmunch.io")
    return "\n".join(out)
