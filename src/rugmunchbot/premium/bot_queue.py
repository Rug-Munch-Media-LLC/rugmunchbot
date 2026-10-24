"""Premium watch deltas -> the bot's alert queue, one item per watching chat.

The bot already drains ``bot:alerts:queue`` every 30s and renders
``token.monitor_change`` items as a compact card plus a scan deep link, so a
premium delta needs no second transport. This module decides only three
things: that the change is genuinely new (alert rows were inserted), who to
tell, and which evidence travels with it.

Evidence rule: every bullet cites an observed number or address from the two
snapshots. ``diff_snapshots`` already refuses unknown -> unknown transitions;
this module additionally drops any bullet it cannot support with a value, so
a card never says "changed" without saying "from what to what".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.premium.watch_rows import describe_event

if TYPE_CHECKING:
    from collections.abc import Iterable

#: Same LIST the bot's alert worker pops (see scanner/webhooks/telegram_bridge).
QUEUE_KEY = "bot:alerts:queue"
#: Item type the bot's formatter already renders as a bulleted change card.
ITEM_TYPE = "token.monitor_change"

_LEAD = {
    "cashed_out": "Top holder cashed out",
    "lp_unlocked": "Liquidity lock gone",
    "new_bundle_wallet": "New bundled wallet",
    "authority_changed": "Token authority changed",
}


def _short(value: Any) -> str:
    text = str(value or "")
    return f"{text[:4]}...{text[-4:]}" if len(text) > 12 else (text or "unknown")


def _evidence(event: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    """The numbers and addresses that justify one event; unknowns are omitted."""
    kind = str(event.get("kind"))
    lines = [f"{_LEAD.get(kind, kind)}: {describe_event(event)}"]
    if kind == "cashed_out" and event.get("before_pct") is not None:
        lines.append(f"Share: {event['before_pct']:.2f}% before")
    if kind == "lp_unlocked":
        before, after = event.get("before_usd"), event.get("after_usd")
        if before is not None and after is not None:
            lines.append(f"Liquidity: ${before:,.0f} -> ${after:,.0f}")
    if kind == "new_bundle_wallet":
        bundles = snapshot.get("bundle_wallets") or []
        if bundles:
            lines.append(f"Bundled set: {len(bundles)} wallets now")
    if kind == "authority_changed":
        lines.append(
            f"{event.get('field', 'authority')}: {_short(event.get('before_value'))} -> {_short(event.get('after_value'))}"
        )
    return lines


def _fingerprint(token: str, events: Iterable[dict[str, Any]]) -> str:
    """Deterministic id for the cycle, so a retry cannot page twice."""
    marks = sorted(
        f"{e.get('kind')}:{e.get('wallet') or e.get('field')}:{e.get('after_value') or e.get('after_pct')}"
        for e in events
    )
    return f"premium:{token}:{'|'.join(marks)}"[:240]


async def enqueue_watch_alerts(
    redis: Any,
    token: str,
    chain: str,
    symbol: str,
    events: list[dict[str, Any]],
    snapshot: dict[str, Any],
    watchers: Iterable[str],
) -> int:
    """Queue one alert per watching chat for this cycle's events.

    Args:
        redis: async Redis client (the same one holding watches/snapshots).
        token: mint or contract address the watch is on.
        chain: chain the token lives on.
        symbol: ticker for the card header; empty renders as the address.
        events: non-empty ``diff_snapshots`` output for this cycle.
        snapshot: the current snapshot, used for evidence context.
        watchers: chat ids watching ``token`` at cycle time.

    Returns:
        How many queue items were pushed. Non-numeric chat ids (web users)
        are skipped: the bot's queue addresses Telegram chats only.
    """
    if not events:
        return 0
    chats = [str(chat) for chat in watchers if str(chat).isdigit()]
    if not chats:
        return 0
    changes: list[str] = []
    for event in events:
        changes.extend(_evidence(event, snapshot))
    item = {
        "type": ITEM_TYPE,
        "event_id": _fingerprint(token, events),
        "payload": {
            "token_address": token,
            "token_symbol": symbol or "",
            "chain": chain,
            "changes": changes,
            "captured_at": snapshot.get("captured_at"),
            "holder_count": (snapshot.get("holders") or {}).get("count"),
            "liquidity_usd": (snapshot.get("lp") or {}).get("liquidity_usd"),
        },
        "created_at": datetime.now(UTC).isoformat(),
    }
    pushed = 0
    for chat in chats:
        await redis.lpush(QUEUE_KEY, json.dumps({**item, "user_id": int(chat)}))
        pushed += 1
    return pushed
