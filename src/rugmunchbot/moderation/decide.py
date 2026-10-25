"""Combine state, text rules, raid state and address verdicts into ONE decision.

This module is the single decision point the gate and the dry-run harness both
call, so what is proven offline is exactly what runs live.

Raid containment is deliberately narrow: only an UNVERIFIED, non-trusted sender
is actioned while a raid is active. Verified members keep talking during a raid,
which is the whole point of an entrance gate — the room is protected, not
silenced.
"""

from __future__ import annotations

import hashlib

from rugmunchbot.moderation import address_rule, guard_settings, raidmode, rules, state, trust
from rugmunchbot.moderation.models import Action, AddressVerdict, MsgContext, worst


def digest(text: str) -> str:
    """Stable hash of a normalised message — used for repeat detection."""
    return hashlib.sha256(" ".join((text or "").lower().split()).encode()).hexdigest()[:32]


def _raid_containment(ctx: MsgContext, policy) -> Action | None:
    """Feed the flood window and return a containment action while a raid holds."""
    settings = guard_settings.for_chat(ctx.chat_id)
    signal = raidmode.note_message(ctx.chat_id, settings, dry_run=policy.dry_run)
    active = raidmode.is_active(ctx.chat_id)
    if not (signal.triggered or active) or policy.dry_run:
        return None
    reason = signal.reason or (raidmode.status(ctx.chat_id) or {}).get("reason", "raid active")
    if state.is_verified(ctx.chat_id, ctx.user_id) or trust.is_trusted(ctx.chat_id, ctx.user_id):
        return None
    action = settings.raid_message_action
    return Action(
        rule="raid_containment",
        action=action,
        reason=f"raid active ({reason}); sender not verified",
        delete=action == "delete",
        mute_minutes=policy.mute_minutes,
        meta={"raid_window_s": signal.window_s, "raid_count": signal.count},
    )


def decide(
    ctx: MsgContext,
    policy,
    verdicts: dict[str, AddressVerdict] | None = None,
) -> Action | None:
    """Return the action to take, or None to leave the message alone.

    Exempt users (configured ids and — only when explicitly enabled — chat
    admins) still get the message recorded in the state windows, but never
    actioned: an admin flooding the room must not mute the room's admins.
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
    raid_rule = _raid_containment(ctx, policy)
    decision = worst(text_rule, addr_rule, raid_rule)
    if decision is not None:
        decision.meta.setdefault("chat_velocity", stats.chat_velocity)
        decision.meta.setdefault("user_velocity", stats.user_velocity)
    return decision


__all__ = ["decide", "digest"]
