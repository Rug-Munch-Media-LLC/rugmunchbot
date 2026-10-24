"""Channel publishing for the smart-money surface (dry-run by default).

Live posting requires BOTH ``RMI_SMARTMONEY_POST`` (explicit opt-in) and a
resolved channel id for the ``alpha`` role (@rmialpha — "Whale & Alpha
Signals"). A dry run resolves the channel but never touches Telegram.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from rugmunchbot.config._channels import _resolve_channel_id
from rugmunchbot.partners import with_partner_strip

from ._premium import premium_status
from .events import EventGate

GATE = EventGate()

_OPT_IN = ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class PublishOptions:
    """Everything a post needs besides its text and its event key.

    One typed object instead of eight keywords: the gate, the channel role,
    the partner lane and the premium evidence travel together, so a new
    routing knob never widens the ``publish`` signature again.
    """

    bot: Any = None
    role: str = "alpha"
    gate: EventGate | None = None
    lane: str | None = None
    chain: str = ""
    token: str = ""
    premium: bool = False
    item: dict[str, Any] | None = None


def posting_enabled() -> bool:
    """True only when the operator explicitly opted in to live posting."""
    return os.getenv("RMI_SMARTMONEY_POST", "").strip().lower() in _OPT_IN


def _envelope(key: str, opts: PublishOptions, verdict: str) -> dict[str, Any]:
    """The result envelope shared by every exit path (held, dry run or sent)."""
    return {
        "key": key,
        "role": opts.role,
        "chat_id": _resolve_channel_id(opts.role),
        "gate": verdict,
        "dry_run": not posting_enabled(),
    }


def _attach_premium(opts: PublishOptions, result: dict[str, Any]) -> bool:
    """Attach the evidence verdict; True when the post may ship as premium.

    Non-premium posts always pass. A premium post gets its ``premium`` block
    on the envelope either way, so a HOLD is auditable rather than silent.
    """
    if not opts.premium:
        return True
    status = premium_status(opts.item)
    result["premium"] = status
    return bool(status["eligible"])


async def publish(post: Any, *, key: str, opts: PublishOptions | None = None) -> dict[str, Any]:
    """Gate, then (opt-in only) send one post. Returns a result envelope.

    ``opts.premium=True`` routes the post to the paid lane and applies the
    evidence gate: an item without on-chain proof, a named source, a
    timestamp and a "why it matters" line is HELD (``held_low_signal``)
    instead of shipping as premium — the $5 tier sells proof, not vibes.
    """
    options = opts or PublishOptions()
    verdict = (options.gate or GATE).allow(key)
    result = _envelope(key, options, verdict)
    premium_ok = _attach_premium(options, result)
    if verdict != "ok":
        return {**result, "status": "suppressed"}
    if not premium_ok:
        return {**result, "status": "held_low_signal"}
    if result["dry_run"] or options.bot is None or not result["chat_id"]:
        return {**result, "status": "dry_run", "chars": len(str(post))}
    text = with_partner_strip(
        str(post), lane=options.lane, chain=options.chain, token=options.token
    )
    await options.bot.send_message(
        chat_id=result["chat_id"], text=text, parse_mode="HTML", disable_web_page_preview=True
    )
    return {**result, "status": "sent"}
