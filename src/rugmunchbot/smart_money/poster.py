"""Channel publishing for the smart-money surface (dry-run by default).

Live posting requires BOTH ``RMI_SMARTMONEY_POST`` (explicit opt-in) and a
resolved channel id for the ``alpha`` role (@rmialpha — "Whale & Alpha
Signals"). A dry run resolves the channel but never touches Telegram.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.config._channels import _resolve_channel_id
from rugmunchbot.partners import with_partner_strip

from ._premium import premium_status
from .events import EventGate

GATE = EventGate()

_OPT_IN = ("1", "true", "yes", "on")


def posting_enabled() -> bool:
    """True only when the operator explicitly opted in to live posting."""
    return os.getenv("RMI_SMARTMONEY_POST", "").strip().lower() in _OPT_IN


async def publish(
    post: Any,
    *,
    key: str,
    bot: Any = None,
    role: str = "alpha",
    gate: EventGate | None = None,
    lane: str | None = None,
    chain: str = "",
    token: str = "",
    premium: bool = False,
    item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate, then (opt-in only) send one post. Returns a result envelope.

    ``premium=True`` routes the post to the paid lane and applies the
    evidence gate: an item without on-chain proof, a named source, a
    timestamp and a "why it matters" line is HELD (``held_low_signal``)
    instead of shipping as premium — the $5 tier sells proof, not vibes.
    """
    verdict = (gate or GATE).allow(key)
    chat_id = _resolve_channel_id(role)
    result: dict[str, Any] = {
        "key": key,
        "role": role,
        "chat_id": chat_id,
        "gate": verdict,
        "dry_run": not posting_enabled(),
    }
    if premium:
        result["premium"] = premium_status(item)
    if verdict != "ok":
        return {**result, "status": "suppressed"}
    if premium and not result["premium"]["eligible"]:
        return {**result, "status": "held_low_signal"}
    if result["dry_run"] or bot is None or not chat_id:
        return {**result, "status": "dry_run", "chars": len(str(post))}
    text = with_partner_strip(str(post), lane=lane, chain=chain, token=token)
    await bot.send_message(
        chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True
    )
    return {**result, "status": "sent"}
