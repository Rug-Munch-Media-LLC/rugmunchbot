"""Pick the launchpad calls worth a post from the normalised call feed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.smart_money.events import event_key

from .plan import PostPlan
from .render_call import render_call

if TYPE_CHECKING:
    from rugmunchbot.smart_money.types import Feed


def _liquidity(row: dict[str, Any]) -> float:
    try:
        return float(row.get("liquidity_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def select_calls(feed: Feed, limit: int = 1) -> list[PostPlan]:
    """Deepest-liquidity launches first; the contract address is the identity.

    A caller row without an address or symbol has no stable event key and is
    dropped — an unidentifiable call can never be deduped.
    """
    rows = [r for r in feed.items if r.get("address") or r.get("symbol")]
    rows.sort(key=_liquidity, reverse=True)
    plans: list[PostPlan] = []
    for row in rows[: max(1, limit)]:
        ident = {"market_id": row.get("address"), "symbol": row.get("symbol")}
        plans.append(
            PostPlan(
                lane="call",
                event_key=event_key("call", ident),
                post=render_call(feed, row),
                source=feed.source,
                observed_at=feed.updated_at,
            )
        )
    return plans
