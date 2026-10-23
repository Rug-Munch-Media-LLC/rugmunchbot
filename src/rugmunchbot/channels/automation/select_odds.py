"""Pick prediction-market odds SHIFTS — never a level dressed as a shift."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.smart_money.events import event_key
from rugmunchbot.smart_money.render_prediction import render_prediction

from .plan import PostPlan

if TYPE_CHECKING:
    from rugmunchbot.smart_money.types import Feed


def _shift(row: dict[str, Any]) -> float:
    try:
        return abs(float(row.get("delta") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def select_odds(feed: Feed, limit: int = 1) -> list[PostPlan]:
    """Only markets whose payload supplied a real delta.

    A market with no 24h baseline is not a shift: it is skipped rather than
    rendered with an invented move. Kalshi rows are aggregate-only, so any
    wallet identity is stripped before the post is built.
    """
    rows: list[dict[str, Any]] = []
    for row in feed.items:
        if row.get("delta") is None or _shift(row) <= 0.0:
            continue
        if str(row.get("source") or "").lower() == "kalshi":
            row = {**row, "wallet_identity": None}
        rows.append(row)
    rows.sort(key=_shift, reverse=True)
    return [
        PostPlan(
            lane="odds",
            event_key=event_key("odds", row),
            post=render_prediction(feed, row),
            source=feed.source,
            observed_at=feed.updated_at,
            risk_note="aggregate book — Kalshi carries no wallet identity",
            chain=str(row.get("chain") or ""),
        )
        for row in rows[: max(1, limit)]
    ]
