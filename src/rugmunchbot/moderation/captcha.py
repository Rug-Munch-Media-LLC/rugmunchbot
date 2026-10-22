"""New-member gate: captcha (button or short math), welcome, timeout handoff.

Dry-run behaviour: the join/captcha event is audited and NOTHING is sent and
nobody is muted. Live behaviour: restrict the newcomer, post the challenge,
schedule the timeout (see :mod:`timeout`), and greet on success.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation import audit, challenge, messages, state, timeout
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


async def handle_new_members(update: Any, context: Any) -> bool:
    """Gate every newcomer. Returns True when the update was consumed."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return False
    policy = load_chat_policy(chat.id)
    if policy is None:
        return False
    for member in message.new_chat_members:
        if member.is_bot:
            continue
        state.mark_member(chat.id, member.id)
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
        if policy.dry_run:
            timeout.schedule(context, chat.id, member.id, policy)
            continue
        await _gate_member(context.bot, chat, member, policy)
        timeout.schedule(context, chat.id, member.id, policy)
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
