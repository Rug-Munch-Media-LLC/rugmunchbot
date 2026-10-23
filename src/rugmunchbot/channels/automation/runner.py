"""Run one content lane: fetch → select → gate → publish. Never raises.

Dry-run is the default: ``publish`` only sends when the operator opted in
with ``RMI_SMARTMONEY_POST`` and a channel id resolves. A scheduled poster
must never crash the JobQueue, so every failure returns an ``error`` envelope
instead of propagating.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.channels import scheduler_state
from rugmunchbot.smart_money import fetch_hl, fetch_moves, fetch_predictions, publish

from .gate import LaneGate
from .lane_call import fetch_calls
from .select_call import select_calls
from .select_liquidation import select_liquidations
from .select_move import select_moves
from .select_odds import select_odds

if TYPE_CHECKING:
    from rugmunchbot.smart_money.types import Feed

GATE = LaneGate()

#: lane → (fetch coroutine, candidate selector). The only lane registry.
LANES = {
    "move": (fetch_moves, select_moves),
    "liquidation": (fetch_hl, select_liquidations),
    "odds": (fetch_predictions, select_odds),
    "call": (fetch_calls, select_calls),
}

LANE_NAMES = tuple(LANES)


def _envelope(lane: str) -> dict[str, Any]:
    return {
        "lane": lane,
        "status": "error",
        "fetched": 0,
        "candidates": 0,
        "posted": 0,
        "suppressed": [],
        "note": "",
        "tried": [],
        "error": "",
    }


async def run_lane(
    lane: str,
    *,
    bot: Any = None,
    role: str = "alpha",
    limit: int = 1,
    feed: Feed | None = None,
    gate: LaneGate | None = None,
) -> dict[str, Any]:
    """Run *lane* once. *feed* injects a pre-fetched Feed (tests / dry runs)."""
    result = _envelope(lane)
    try:
        fetcher, selector = LANES[lane]
        current = feed if feed is not None else await fetcher()
        result["fetched"] = len(current.items)
        result["note"] = current.note
        result["tried"] = list(current.tried)
        plans = selector(current, limit)
        result["candidates"] = len(plans)
        if not plans:
            result["status"] = "empty"
            return result
        statuses: list[str] = []
        for plan in plans:
            sent = await publish(
                plan.post,
                key=plan.event_key,
                bot=bot,
                role=role or plan.channel_role,
                gate=gate or GATE,
            )
            statuses.append(str(sent.get("status") or "unknown"))
            if sent.get("status") == "sent":
                scheduler_state.spawn_persist(role or plan.channel_role, plan.event_key)
            if sent.get("status") == "suppressed":
                result["suppressed"].append({"key": plan.event_key, "gate": sent.get("gate")})
        result["posted"] = statuses.count("sent")
        result["status"] = statuses[0] if len(set(statuses)) == 1 else "mixed"
    except Exception as exc:  # a lane must never crash the JobQueue
        result["error"] = str(exc)[:200]
    return result
