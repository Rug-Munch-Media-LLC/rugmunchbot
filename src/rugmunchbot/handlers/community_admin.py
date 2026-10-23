"""``/setup`` — channel-owner settings for the current chat.

Owner/admin only: a fleet owner, an id in the chat's explicit admin list, or a
real Telegram administrator of the chat. The command is deliberately NOT in the
BotFather menu — it is declared in ``bot._menu_guard.ADMIN_MENU_COMMANDS``.

The command grammar lives in :mod:`rugmunchbot.community.commands`; this module
only resolves the chat and the Redis client, gates the caller, and replies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode

from rugmunchbot.community.auth import require_chat_admin
from rugmunchbot.community.commands import run_setup_command
from rugmunchbot.community.store import COMMUNITY_CLIENT_KEY
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.community_admin")


def _injected_client(ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    """Redis client injected via ``Application.bot_data`` (tests, multi-tenant)."""
    bot_data = getattr(ctx, "bot_data", None)
    if isinstance(bot_data, dict):
        return bot_data.get(COMMUNITY_CLIENT_KEY)
    return None


async def cmd_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show or change this chat's community settings."""
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or user is None or message is None:
        return

    client = _injected_client(ctx)
    allowed, denial = await require_chat_admin(chat.id, user.id, getattr(ctx, "bot", None), client)
    if not allowed:
        await _reply(message, denial or "⛔ Admin only.")
        return

    try:
        text = await run_setup_command(chat.id, user.id, list(ctx.args or []), client)
    except Exception:  # a settings bug must not stall the update loop
        logger.exception("setup_command_failed chat_id=%s", chat.id)
        text = "⛔ Something went wrong updating settings. Nothing was changed."
    await _reply(message, text)


async def _reply(message: Any, text: str) -> None:
    """Send HTML, falling back to plain text if the markup is rejected."""
    try:
        await message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception:  # plain-text fallback is the last resort
        logger.warning("setup_reply_html_failed", exc_info=True)
        await message.reply_text(text)


def register_community_admin(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register ``/setup`` through the shared flood gate + activity tracker."""
    from rugmunchbot.bot._commands import _reg

    _reg(app, "setup", cmd_setup)


__all__ = ["cmd_setup", "register_community_admin"]
