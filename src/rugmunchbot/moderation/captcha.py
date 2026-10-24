"""New-member gate: entrance risk → quarantine → captcha (button or math).

Order matters. A join is first scored by :mod:entrance (account-age proxy,
profile/name heuristics, bots). A risky join is quarantined and held for a human
(see :mod:quarantine) instead of being admitted to the captcha flow — a raid bot
that never taps a button still occupies the room. Everyone else gets the normal
captcha, and the join itself feeds the raid detector.

Dry-run behaviour: the join/risk/captcha event is audited and NOTHING is sent
and nobody is muted. Live behaviour: restrict, post the challenge, schedule the
timeout (see :mod:timeout), greet on success.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation import (
    audit,
    challenge,
    entrance,
    guard_settings,
    messages,
    quarantine,
    raidmode,
    state,
    timeout,
    trust,
)
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.captcha")

CAPTCHA_PREFIX = "modcap"


def callback_data(chat_id: int, user_id: int, answer: int | None = None) -> str:
    base = f"{CAPTCHA_PREFIX}:{chat_id}:{user_id}"
    return base if answer is None else f"{base}:{answer}"


def parse_callback(data: str) -> tuple[int, int, int | None] | None:
    parts = (data or "").split(":")
    if len(parts) not in {3, 4} or parts[0] != CAPTCHA_PREFIX:
        return None
    try:
        answer = int(parts[3]) if len(parts) == 4 else None
        return int(parts[1]), int(parts[2]), answer
    except ValueError:
        return None


def _audit(chat_id: int, user_id: int, rule: str, action: str, reason: str, dry: bool) -> None:
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat_id,
            chat_title="",
            user_id=user_id,
            username="",
            rule=rule,
            action=action,
            reason=reason,
            matched="",
            dry_run=dry,
            applied=False,
            actor="auto:entrance",
        )
    )


async def _gate_one(update: Any, context: Any, member: Any, chat: Any, policy: Any) -> None:
    """Score one join, then hold it or send it through the captcha."""
    settings = guard_settings.for_chat(chat.id)
    display_name = getattr(member, "full_name", "") or getattr(member, "first_name", "")
    username = getattr(member, "username", "") or ""
    state.mark_member(chat.id, member.id)
    risk = entrance.assess_join(
        user_id=member.id,
        username=username,
        display_name=display_name,
        is_bot=bool(getattr(member, "is_bot", False)),
        settings=settings,
    )
    if risk.action == "quarantine" and not trust.is_trusted(chat.id, member.id):
        await quarantine.hold(
            context.bot, chat.id, member.id, risk, settings, policy=policy, username=username
        )
        return
    if getattr(member, "is_bot", False):
        return
    _audit(
        chat.id,
        member.id,
        "new_member_gate",
        "mute" if policy.new_member_captcha else "welcome",
        f"{policy.captcha_type} captcha, {policy.captcha_timeout_s}s"
        if policy.new_member_captcha
        else "welcome only",
        policy.dry_run,
    )
    if not policy.dry_run:
        await _gate_member(context.bot, chat, member, policy)
    timeout.schedule(context, chat.id, member.id, policy)


async def handle_new_members(update: Any, context: Any) -> bool:
    """Gate every newcomer. Returns True when the update was consumed."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return False
    policy = load_chat_policy(chat.id)
    if policy is None:
        return False
    settings = guard_settings.for_chat(chat.id)
    signal = raidmode.note_join(chat.id, settings, dry_run=policy.dry_run)
    if signal.triggered:
        _audit(chat.id, 0, "raid_detected", "lockdown", signal.reason, policy.dry_run)
    for member in message.new_chat_members:
        if trust.is_trusted(chat.id, member.id):
            state.mark_verified(chat.id, member.id)
        await _gate_one(update, context, member, chat, policy)
    return True


async def _gate_member(bot: Any, chat: Any, member: Any, policy: Any) -> None:
    from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup

    try:
        await bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=member.id,
            permissions=ChatPermissions(can_send_messages=False),
            use_independent_chat_permissions=True,
        )
    except Exception:
        logger.info("captcha mute failed (bot may lack rights)", exc_info=True)
    if policy.captcha_type == "math":
        question, options, _ = challenge.challenge(chat.id, member.id)
        text = messages.captcha_math(question, max(1, policy.captcha_timeout_s // 60 or 1))
        row = [
            InlineKeyboardButton(str(opt), callback_data=callback_data(chat.id, member.id, opt))
            for opt in options
        ]
    else:
        text = messages.captcha_prompt(max(1, policy.captcha_timeout_s // 60 or 1))
        row = [
            InlineKeyboardButton("I'm human ✅", callback_data=callback_data(chat.id, member.id))
        ]
    try:
        await bot.send_message(
            chat_id=chat.id,
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([row]),
        )
    except Exception:
        logger.warning("captcha prompt send failed", exc_info=True)


__all__ = [
    "CAPTCHA_PREFIX",
    "callback_data",
    "handle_new_members",
    "parse_callback",
]
