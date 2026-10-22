"""Entrance-gate timeout: mute or kick a newcomer who never solved the captcha.

Scheduling is the single public entry; the job body stays private so both the
live bot and the dry harness drive exactly the same code path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.moderation import audit, messages, state
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.timeout")


async def _close_gate(bot: Any, chat_id: int, user_id: int, policy: Any) -> dict[str, Any]:
    if state.is_verified(chat_id, user_id):
        return {}
    if policy.dry_run:
        entry = audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat_id,
            chat_title="",
            user_id=user_id,
            username="",
            rule="captcha_timeout",
            action="kick" if policy.captcha_kick else "mute",
            reason=f"dry-run: captcha not solved within {policy.captcha_timeout_s}s",
            matched="",
            dry_run=True,
            applied=False,
            actor="auto:captcha_timeout",
        )
        audit.record(entry)
        return entry
    errors: list[str] = []
    kicked = False
    if policy.captcha_kick:
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            kicked = True
        except Exception as exc:
            errors.append(f"kick: {type(exc).__name__}")
    else:
        from telegram import ChatPermissions

        until = datetime.now(UTC) + timedelta(minutes=policy.captcha_mute_minutes)
        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
                use_independent_chat_permissions=True,
            )
        except Exception as exc:
            errors.append(f"mute: {type(exc).__name__}")
    entry = audit.build_entry(
        action_id=audit.new_action_id(),
        chat_id=chat_id,
        chat_title="",
        user_id=user_id,
        username="",
        rule="captcha_timeout",
        action="kick" if kicked else "mute",
        reason=f"captcha not solved within {policy.captcha_timeout_s}s",
        matched="",
        dry_run=False,
        applied=not errors,
        actor="auto:captcha_timeout",
        error="; ".join(errors) or None,
    )
    audit.record(entry)
    if not errors:
        try:
            await bot.send_message(
                chat_id=chat_id, text=messages.captcha_failed(kicked), parse_mode="HTML"
            )
        except Exception:
            logger.info("captcha failure notice not sent", exc_info=True)
    return entry


def schedule(context: Any, chat_id: int, user_id: int, policy: Any) -> bool:
    """Schedule the gate timeout. False when the captcha is off or no queue."""
    if not policy.new_member_captcha:
        return False
    queue = getattr(context, "job_queue", None)
    if queue is None:
        logger.warning("no job_queue; captcha timeout cannot be enforced")
        return False

    async def _expire(job_context: Any) -> None:
        bot = getattr(job_context, "bot", None) or context.bot
        await _close_gate(bot, chat_id, user_id, policy)

    queue.run_once(_expire, when=policy.captcha_timeout_s, name=f"modcap:{chat_id}:{user_id}")
    return True


__all__ = ["schedule"]
