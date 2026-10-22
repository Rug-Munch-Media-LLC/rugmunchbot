"""Combine state, text rules and address verdicts into ONE decision.

This module is the single decision point the gate and the dry-run harness
both call, so what is proven offline is exactly what runs live.
"""

from __future__ import annotations

import hashlib

from rugmunchbot.moderation import address_rule, rules, state
from rugmunchbot.moderation.models import Action, AddressVerdict, MsgContext, worst


def digest(text: str) -> str:
    """Stable hash of a normalised message — used for repeat detection."""
    return hashlib.sha256(" ".join((text or "").lower().split()).encode()).hexdigest()[:32]


def decide(
    ctx: MsgContext,
    policy,
    verdicts: dict[str, AddressVerdict] | None = None,
) -> Action | None:
    """Return the action to take, or None to leave the message alone.

    Exempt users (configured ids and — unless disabled — chat admins) still
    get the message recorded in the state windows, but never actioned: an
    admin flooding the room must not mute the room's admins.
    """
    stats = state.note_message(
        ctx.chat_id,
        ctx.user_id,
        digest(ctx.text),
        media=ctx.media is not None,
        window_s=policy.velocity_window_s,
        dup_window_s=policy.dup_window_s,
    )
    if policy.is_exempt(ctx):
        return None

    text_rule = rules.evaluate_text(ctx, policy, stats)
    addr_rule = address_rule.evaluate_address(verdicts or {}, policy)
    decision = worst(text_rule, addr_rule)
    if decision is not None:
        decision.meta.setdefault("chat_velocity", stats.chat_velocity)
        decision.meta.setdefault("user_velocity", stats.user_velocity)
    return decision


__all__ = ["decide", "digest"]
