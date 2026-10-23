"""Authorization for channel-owner settings.

Deny-by-default: a user is allowed only when they are a fleet owner, listed in
the chat's explicit ``admins`` list, or a real Telegram administrator of the
chat. Resolving Telegram admins is best-effort — a failed lookup denies, it
never grants.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.community.store import load_settings
from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.community.auth")

DENIED = "⛔ Admin only. Ask a chat administrator to run /setup admins add <your id>."


async def is_chat_admin(
    chat_id: int,
    user_id: int,
    bot: Any = None,
    client: Any = None,
) -> bool:
    """Return ``True`` when *user_id* may administer *chat_id*."""
    if is_owner(user_id):
        return True
    settings = await load_settings(chat_id, client)
    if user_id in settings.admins:
        return True
    if bot is None:
        return False
    try:
        members = await bot.get_chat_administrators(chat_id)
    except Exception:  # a lookup failure must deny, not crash
        logger.warning("chat_admin_lookup_failed chat_id=%s", chat_id, exc_info=True)
        return False
    for member in members:
        user = getattr(member, "user", None)
        if user is not None and getattr(user, "id", None) == user_id:
            return True
    return False


async def require_chat_admin(
    chat_id: int,
    user_id: int,
    bot: Any = None,
    client: Any = None,
) -> tuple[bool, str | None]:
    """Gate a handler: ``(False, message)`` when the caller is not an admin."""
    if await is_chat_admin(chat_id, user_id, bot, client):
        return True, None
    logger.info("community_admin_denied chat_id=%s user_id=%s", chat_id, user_id)
    return False, DENIED
