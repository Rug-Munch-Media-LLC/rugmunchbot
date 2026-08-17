#!/usr/bin/env python3
"""RugMunchBot -- owner admin console for the free-text AI pipeline.

Split out of ``_routing.py`` during the de-godding pass. This module owns
the admin reply-keyboard handling that lives at the TOP of
``ai_chat_handler``: the persistent admin reply keyboard attached to the
owner's private DM (``_ensure_admin_reply_kb``) and the one-tap routing of
its button presses to the matching ``/admin_*`` commands
(``_dispatch_admin_button``).

``_routing.py`` re-exports ``_ADMIN_BUTTON_MAP``, ``_ensure_admin_reply_kb``
and ``_dispatch_admin_button``, so every public name and signature is
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.handlers.message import (
    admin_kb_is_sent,
    admin_kb_mark_sent,
    admin_kb_reset,
)
from app.domains.telegram.rugmunchbot.keyboards import owner_admin_reply_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# OWNER ADMIN CONSOLE (ported from handlers/message.py)
# Persistent reply keyboard pinned below the input box in private chats.
# Button labels are plain text; they are routed to the matching /admin_*
# command so @cryptorugmunch always has one-tap admin access.
# ══════════════════════════════════════════════
_ADMIN_BUTTON_MAP: dict[str, str] = {
    "\U0001f6e1\ufe0f Admin Dashboard": "/admin",
    "\U0001f4ca Stats": "/admin_stats",
    "\U0001f4e2 Broadcast": "/admin_broadcast",
    "\U0001f465 User Lookup": "/admin_user",
    "\U0001f504 Bot ON/OFF": "/admin_toggle",
    "\U0001f4ac Chat ON/OFF": "/admin_toggle_chat",
    "\U0001f3d9\ufe0f Main Menu": "/start",
}


async def _ensure_admin_reply_kb(update: Update) -> None:
    """Attach the persistent admin reply keyboard once per owner DM."""
    chat = update.effective_chat
    if chat is None or admin_kb_is_sent(chat.id) or update.message is None:
        return
    admin_kb_mark_sent(chat.id)
    try:
        await update.message.reply_text(
            "\U0001f6e1\ufe0f <b>Admin console</b>\n"
            "Tap a button below for quick admin actions \u2014 they stay pinned here.",
            parse_mode=ParseMode.HTML,
            reply_markup=owner_admin_reply_kb(),
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        admin_kb_reset(chat.id)
        logger.debug("admin reply keyboard send failed", exc_info=True)


async def _dispatch_admin_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Route an admin reply-keyboard button press to its /admin_* command."""
    from app.domains.telegram.rugmunchbot.handlers.owner import (
        cmd_admin,
        cmd_admin_broadcast,
        cmd_admin_stats,
        cmd_admin_toggle,
        cmd_admin_toggle_chat,
        cmd_admin_user,
    )
    from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_start

    cmd = _ADMIN_BUTTON_MAP[text]
    ctx.args = []
    if cmd == "/admin":
        await cmd_admin(update, ctx)
    elif cmd == "/admin_stats":
        await cmd_admin_stats(update, ctx)
    elif cmd == "/admin_broadcast":
        await cmd_admin_broadcast(update, ctx)
    elif cmd == "/admin_user":
        await cmd_admin_user(update, ctx)
    elif cmd == "/admin_toggle":
        await cmd_admin_toggle(update, ctx)
    elif cmd == "/admin_toggle_chat":
        await cmd_admin_toggle_chat(update, ctx)
    elif cmd == "/start":
        # /start re-attaches the persistent keyboard below the new message.
        admin_kb_reset(update.effective_chat.id)  # type: ignore[union-attr]  # PTB None-able fields
        await cmd_start(update, ctx)


__all__ = [
    "_ADMIN_BUTTON_MAP",
    "_dispatch_admin_button",
    "_ensure_admin_reply_kb",
]
