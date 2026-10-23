"""Admin authorization for community commands: bot owner OR chat admin.

One helper, one policy: every mutating community command calls
:func:`require_admin`, which answers the user itself when the caller is not
allowed. Reads (``/notes``, ``/filters``, ``/get``) stay open to the chat.
"""

from __future__ import annotations

from typing import Any

DENY = "⛔ Chat admins only."


def is_owner(update: Any) -> bool:
    """True when the caller is a configured bot owner."""
    from rugmunchbot.config import OWNER_IDS

    user = getattr(update, "effective_user", None)
    return bool(user is not None and user.id in OWNER_IDS)


async def is_chat_admin(update: Any, context: Any) -> bool:
    """True for bot owners and for administrators/owners of the current chat."""
    if is_owner(update):
        return True
    user, chat = update.effective_user, update.effective_chat
    if user is None or chat is None:
        return False
    try:
        from telegram.constants import ChatMemberStatus

        member = await context.bot.get_chat_member(chat_id=chat.id, user_id=user.id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except Exception:  # a failed lookup must deny, never allow
        return False


async def require_admin(update: Any, context: Any, *, deny: str = DENY) -> bool:
    """Guard a command handler. Replies with *deny* and returns False if blocked."""
    if await is_chat_admin(update, context):
        return True
    message = update.effective_message
    if message is not None:
        await message.reply_text(deny)
    return False


__all__ = ["DENY", "is_chat_admin", "is_owner", "require_admin"]
