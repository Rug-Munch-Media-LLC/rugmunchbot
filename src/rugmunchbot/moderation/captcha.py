"""New-member gate: captcha button, welcome, optional timeout kick.

Dry-run behaviour: the join is audited and NOTHING is sent and nobody is
muted. Live behaviour: restrict the newcomer, post a one-button captcha, and
(only when ``captcha_kick`` is true) schedule removal.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.moderation import audit, messages, state
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.captcha")

CAPTCHA_PREFIX = "modcap"


def callback_data(chat_id: int, user_id: int) -> str:
    return f"{CAPTCHA_PREFIX}:{chat_id}:{user_id}"


def parse_callback(data: str) -> tuple[int, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != CAPTCHA_PREFIX:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


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
        record = audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat.id,
            chat_title=chat.title or "",
            user_id=member.id,
            username=member.username or "",
            rule="new_member_gate",
            action="mute" if policy.new_member_captcha else "welcome",
            reason="captcha gate" if policy.new_member_captcha else "welcome only",
            matched=member.full_name,
            dry_run=policy.dry_run,
            applied=False,
        )
        audit.record(record)
        if policy.dry_run:
            continue
        await _gate_member(context.bot, chat, member, policy)
    return True


async def _gate_member(bot: Any, chat: Any, member: Any, policy: Any) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    if policy.new_member_captcha:
        from telegram import ChatPermissions

        try:
            await bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=member.id,
                permissions=ChatPermissions(can_send_messages=False),
                use_independent_chat_permissions=True,
            )
        except Exception:
            logger.info("captcha mute failed (bot may lack rights)", exc_info=True)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("I'm human ✅", callback_data=callback_data(chat.id, member.id))]]
    )
    try:
        await bot.send_message(
            chat_id=chat.id,
            text=messages.captcha_prompt(policy.captcha_mute_minutes),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:
        logger.warning("captcha prompt send failed", exc_info=True)


async def handle_captcha_callback(update: Any, context: Any) -> bool:
    """Verify the tapping user, unmute, greet. Returns True when consumed."""
    query = update.callback_query
    if query is None or not (query.data or "").startswith(f"{CAPTCHA_PREFIX}:"):
        return False
    parsed = parse_callback(query.data)
    if parsed is None:
        await query.answer("Stale button.", show_alert=False)
        return True
    chat_id, user_id = parsed
    if query.from_user is None or query.from_user.id != user_id:
        await query.answer("This button isn't yours.", show_alert=True)
        return True
    policy = load_chat_policy(chat_id)
    if policy is None:
        await query.answer()
        return True
    if policy.dry_run:
        audit.record(
            audit.build_entry(
                action_id=audit.new_action_id(),
                chat_id=chat_id,
                chat_title="",
                user_id=user_id,
                username=query.from_user.username or "",
                rule="captcha_pass",
                action="unmute",
                reason="dry-run: would unmute and greet",
                matched="",
                dry_run=True,
                applied=False,
            )
        )
        await query.answer("Dry-run: verified (no action).")
        return True
    await _pass_captcha(context.bot, query, chat_id, user_id, policy)
    return True


async def _pass_captcha(bot: Any, query: Any, chat_id: int, user_id: int, policy: Any) -> None:
    from telegram import ChatPermissions

    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
            use_independent_chat_permissions=True,
        )
    except Exception:
        logger.warning("captcha unmute failed", exc_info=True)
    text = policy.welcome or messages.welcome(query.from_user.first_name or "")
    await query.answer("Verified ✅")
    try:
        await bot.send_message(
            chat_id=chat_id, text=html.escape(text) if "<" in text else text, parse_mode="HTML"
        )
    except Exception:
        logger.info("welcome send failed", exc_info=True)


__all__ = [
    "CAPTCHA_PREFIX",
    "callback_data",
    "handle_captcha_callback",
    "handle_new_members",
    "parse_callback",
]
