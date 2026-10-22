"""Cross-chat ban propagation (opt-in per chat).

A ban in one guarded chat is replayed in every id listed in
``propagate_chats``. Best-effort: a chat where the bot is not admin is audited
and skipped, never raised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.moderation import audit
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot.moderation.models import MsgContext

logger = get_logger("rmi_bot.moderation.propagate")


async def ban_everywhere(
    bot: Any, ctx: MsgContext, policy: Any, reason: str, errors: list[str]
) -> list[int]:
    """Ban *ctx.user_id* in every configured peer chat; return the successes."""
    if not policy.propagate_ban:
        return []
    done: list[int] = []
    for chat_id in policy.propagate_chats:
        if chat_id == ctx.chat_id:
            continue
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=ctx.user_id)
        except Exception as exc:
            errors.append(f"propagate:{chat_id}:{type(exc).__name__}")
            logger.info("propagated ban failed", extra={"chat_id": chat_id}, exc_info=True)
            continue
        done.append(chat_id)
        audit.record(
            audit.build_entry(
                action_id=audit.new_action_id(),
                chat_id=chat_id,
                chat_title="",
                user_id=ctx.user_id,
                username=ctx.username,
                rule="ban_propagation",
                action="ban",
                reason=f"propagated from {ctx.chat_id}: {reason}"[:200],
                matched="",
                dry_run=False,
                applied=True,
                actor=f"auto:{ctx.chat_id}",
            )
        )
    return done


__all__ = ["ban_everywhere"]
