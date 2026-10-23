"""Pick the strongest smart-money moves from a normalised feed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.smart_money.events import event_key
from rugmunchbot.smart_money.render_move import render_move

from .plan import PostPlan

if TYPE_CHECKING:
    from rugmunchbot.smart_money.types import Feed


def _size(row: dict[str, Any]) -> float:
    try:
        return float(row.get("amount_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def select_moves(feed: Feed, limit: int = 1) -> list[PostPlan]:
    """Top moves by USD size that carry a wallet — no wallet, no post."""
    rows = [r for r in feed.items if r.get("wallet") and r.get("amount_usd") is not None]
    rows.sort(key=_size, reverse=True)
    return [
        PostPlan(
            lane="move",
            event_key=event_key("move", row),
            post=render_move(feed, row),
            source=feed.source,
            observed_at=feed.updated_at,
        )
        for row in rows[: max(1, limit)]
    ]
