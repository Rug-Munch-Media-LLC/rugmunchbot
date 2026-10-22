"""Captcha verification: ownership check, math answer, pass/fail actions."""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.moderation import audit, challenge, messages, state
from rugmunchbot.moderation.captcha import CAPTCHA_PREFIX, parse_callback
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.captcha_verify")


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


async def handle_captcha_callback(update: Any, context: Any) -> bool:
    """Verify the tapping user, unmute, greet. Returns True when consumed."""
    query = update.callback_query
    if query is None or not (query.data or "").startswith(f"{CAPTCHA_PREFIX}:"):
        return False
    parsed = parse_callback(query.data)
    if parsed is None:
        await query.answer("Stale button.", show_alert=False)
        return True
    chat_id, user_id, answer = parsed
    if query.from_user is None or query.from_user.id != user_id:
        await query.answer("This button isn't yours.", show_alert=True)
        return True
    policy = load_chat_policy(chat_id)
    if policy is None:
        await query.answer()
        return True
    if answer is not None and not challenge.accepts(chat_id, user_id, answer):
        _audit(chat_id, user_id, "captcha_fail", "kick", "wrong answer", policy.dry_run)
        if policy.dry_run:
            await query.answer("Dry-run: would fail the captcha.")
            return True
        await _fail_member(context.bot, query, chat_id, user_id, policy)
        return True
    _audit(chat_id, user_id, "captcha_pass", "unmute", "captcha solved", policy.dry_run)
    if policy.dry_run:
        await query.answer("Dry-run: verified (no action).")
        return True
    state.mark_verified(chat_id, user_id)
    await _pass_captcha(context.bot, query, chat_id, user_id, policy)
    return True


async def _fail_member(bot: Any, query: Any, chat_id: int, user_id: int, policy: Any) -> None:
    from telegram import ChatPermissions

    kicked = False
    try:
        if policy.captcha_kick:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            kicked = True
        else:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                use_independent_chat_permissions=True,
            )
        await bot.send_message(
            chat_id=chat_id, text=messages.captcha_failed(kicked), parse_mode="HTML"
        )
    except Exception:
        logger.info("captcha failure not applied", exc_info=True)
    await query.answer("Failed ❌" if not kicked else "Removed ❌", show_alert=True)


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


__all__ = ["handle_captcha_callback"]
