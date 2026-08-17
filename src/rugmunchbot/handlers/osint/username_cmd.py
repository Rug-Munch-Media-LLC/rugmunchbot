"""/osint <username> — find a username's public accounts (maigret)."""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.osint._common import _TIMEOUTS, _valid_username
from app.domains.telegram.rugmunchbot.handlers.osint._format import (
    _error_text,
    _format_username_result,
    _unavailable_text,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.osint import OsintNotAvailable
from app.osint_tools import OsintNotAvailable as ToolsOsintNotAvailable, lookup_username

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_osint(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/osint <username> — find a username's public accounts (maigret)."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f575\ufe0f <b>Username OSINT</b>\n\n"
            "Usage: /osint <code>username</code>\n\n"
            "Example: /osint <code>cryptorugmunch</code>\n\n"
            "Checks the handle across social platforms (maigret).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    username = ctx.args[0].strip().lstrip("@")
    if not _valid_username(username):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid username</b>\n\n"
            "Usernames must be 1-128 characters with no spaces or path separators.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Searching accounts for</b> <code>{html.escape(username)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await asyncio.to_thread(lookup_username, username, timeout=_TIMEOUTS["username"])
        await msg.edit_text(_format_username_result(result), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except (ToolsOsintNotAvailable, OsintNotAvailable) as exc:
        await msg.edit_text(_unavailable_text(username, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(username, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
