"""Execute a decision against Telegram — dry-run safe, always audited.

``policy.dry_run`` short-circuits EVERY API call: the decision is written to
the audit log with ``applied: false`` and the bot performs no delete, mute,
ban or reply. That is the default for every chat until an operator flips the
one flag (see ``configs/moderation.example.json``).

Telegram errors are swallowed per-operation (recorded in the audit row) so a
missing admin right or a deleted message never raises into the dispatcher.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.moderation import audit
from rugmunchbot.moderation.models import (  # noqa: TC001 — Action is built at runtime
    Action,
    MsgContext,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.actions")

ALERT_THROTTLE_S = 600
_alert_seen: dict[str, float] = {}


def _throttled(key: str, seconds: int = ALERT_THROTTLE_S) -> bool:
    now = time.monotonic()
    last = _alert_seen.get(key, 0.0)
    if now - last < seconds:
        return True
    _alert_seen[key] = now
    return False


async def _try(label: str, coro_factory, errors: list[str]) -> None:
    try:
        await coro_factory()
    except Exception as exc:
        errors.append(f"{label}: {type(exc).__name__}")
        logger.info("moderation op failed", extra={"op": label, "error": str(exc)[:200]})


async def execute(bot: Any, ctx: MsgContext, policy: Any, decision: Action) -> dict[str, Any]:
    """Apply *decision* (or simulate it) and return the audit record."""
    action_id = audit.new_action_id()
    errors: list[str] = []
    applied = False

    if not policy.dry_run:
        if decision.delete and ctx.message_id:
            await _try(
                "delete_message",
                lambda: bot.delete_message(chat_id=ctx.chat_id, message_id=ctx.message_id),
                errors,
            )
        if decision.action == "mute" and decision.mute_minutes:
            until = datetime.now(UTC) + timedelta(minutes=decision.mute_minutes)
            from telegram import ChatPermissions

            await _try(
                "mute",
                lambda: bot.restrict_chat_member(
                    chat_id=ctx.chat_id,
                    user_id=ctx.user_id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                    use_independent_chat_permissions=True,
                ),
                errors,
            )
        if decision.action == "ban":
            await _try(
                "ban",
                lambda: bot.ban_chat_member(chat_id=ctx.chat_id, user_id=ctx.user_id),
                errors,
            )
        if decision.reply and not _throttled(f"reply:{ctx.chat_id}:{decision.rule}"):
            await _try(
                "reply",
                lambda: bot.send_message(
                    chat_id=ctx.chat_id,
                    text=decision.reply,
                    parse_mode="HTML",
                    reply_to_message_id=ctx.message_id,
                    disable_web_page_preview=True,
                ),
                errors,
            )
        applied = not errors

    entry = audit.build_entry(
        action_id=action_id,
        chat_id=ctx.chat_id,
        chat_title=ctx.chat_title,
        user_id=ctx.user_id,
        username=ctx.username,
        rule=decision.rule,
        action=decision.action,
        reason=decision.reason,
        matched=decision.matched,
        dry_run=policy.dry_run,
        applied=applied,
        error="; ".join(errors) or None,
        text=ctx.text,
    )
    audit.record(entry)
    return entry


async def lift(bot: Any, chat_id: int, user_id: int, policy: Any) -> bool:
    """Undo a mute/ban (pardon). Returns True when Telegram accepted it."""
    if policy.dry_run:
        return False
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
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        return True
    except Exception:
        logger.warning("moderation lift failed", exc_info=True)
        return False


__all__ = ["execute", "lift"]
