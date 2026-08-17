"""/email <email> — breach + reputation check (HIBP, emailrep.io)."""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.osint._common import _TIMEOUTS, _valid_email
from app.domains.telegram.rugmunchbot.handlers.osint._format import _error_text, _format_report, _unavailable_text
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.osint import OsintNotAvailable
from app.osint.email import lookup_email

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_email(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/email <email> — breach + reputation check (HIBP, emailrep.io)."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4e7 <b>Email OSINT</b>\n\n"
            "Usage: /email <code>email</code>\n\n"
            "Example: /email <code>victim@example.com</code>\n\n"
            "Breach history (HIBP), pastes and reputation (emailrep.io).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    email = ctx.args[0].strip().lower()
    if not _valid_email(email):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid email address</b>\n\nPass a valid address like <code>name@example.com</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Checking email</b> <code>{html.escape(email)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_email, email, timeout=_TIMEOUTS["email"])
        await msg.edit_text(
            _format_report(report, "\U0001f4e7 <b>Email OSINT</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(email, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(email, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
