"""Pick Hyperliquid liquidation-pressure / funding-squeeze events."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.smart_money.events import event_key
from rugmunchbot.smart_money.render_hl import render_hl

from .plan import PostPlan

if TYPE_CHECKING:
    from rugmunchbot.smart_money.types import Feed

_EVENT_KINDS = frozenset({"cluster", "squeeze"})


def _number(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rank(row: dict[str, Any]) -> tuple[int, float, float]:
    """Liquidation maps first, then funding squeezes, each by size.

    A cluster is an actual liquidation reading; a squeeze is a funding
    warning. Ranking them on one number would let a large-squeeze OI hide a
    real liquidation cluster, so kind is the first key.
    """
    is_cluster = 1 if row.get("kind") == "cluster" else 0
    return (is_cluster, _number(row, "liquidation_pressure"), _number(row, "open_interest_usd"))


def select_liquidations(feed: Feed, limit: int = 1) -> list[PostPlan]:
    """Liquidation maps and funding squeezes, largest pressure first.

    A plain 24h mover is not a liquidation event and is not selected here;
    it belongs to the market lane. Market-level data only — the renderer
    states that there is no wallet attribution.
    """
    rows = [r for r in feed.items if r.get("kind") in _EVENT_KINDS]
    rows.sort(key=_rank, reverse=True)
    return [
        PostPlan(
            lane="liquidation",
            event_key=event_key("liquidation", row),
            post=render_hl(feed, row),
            source=feed.source,
            observed_at=feed.updated_at,
            chain=str(row.get("chain") or ""),
            token=str(row.get("token_address") or row.get("token") or ""),
        )
        for row in rows[: max(1, limit)]
    ]
