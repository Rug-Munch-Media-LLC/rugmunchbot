"""Render one prediction-market odds level / shift as a channel post."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.channels.formatters._primitives import NewsPost

from ._fmt import esc, pts, usd
from .render_base import compose

_RISK = (
    "aggregate market odds, not a forecast — Kalshi carries no wallet identity "
    "and no liquidations. Thin books misprice; odds ≠ probability. Not financial advice."
)


def render_prediction(feed: Any, market: dict[str, Any]) -> NewsPost:
    """Build the post for one normalised market (shift only when supplied)."""
    question = esc(str(market.get("question") or "?")[:110])
    headline = f"🔮 <b>ODDS · {question}</b>"

    yes = float(market.get("yes") or 0.0) * 100
    shift = pts(market.get("delta"))
    evidence = [f"YES <b>{yes:.1f}%</b>" + (f" ({shift} 24h)" if shift else " (no 24h baseline)")]
    evidence.append(f"Volume: {usd(market.get('volume_usd'))}")
    if market.get("ends_at"):
        evidence.append(f"Resolves: {esc(str(market['ends_at'])[:10])}")
    evidence.append(f"Venue: {esc(market.get('source'))}")

    return compose(
        headline=headline,
        evidence=evidence,
        source=f"{feed.source} via rmi-backend (aggregate book)",
        updated=feed.updated_at,
        risk=_RISK,
        lane="predictions",
        payload=market,
    )
