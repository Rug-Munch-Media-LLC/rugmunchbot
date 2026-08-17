"""/phone <phone> — validation, carrier, region and line-type risk."""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.osint._common import _TIMEOUTS, _valid_phone
from app.domains.telegram.rugmunchbot.handlers.osint._format import _error_text, _format_report, _unavailable_text
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.osint import OsintNotAvailable
from app.osint.phone import lookup_phone

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/phone <phone> — validation, carrier, region and line-type risk."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4f1 <b>Phone OSINT</b>\n\n"
            "Usage: /phone <code>number</code>\n\n"
            "Example: /phone <code>+14155552671</code>\n\n"
            "Validation, carrier, region, timezone and line-type risk.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    phone = ctx.args[0].strip()
    if not _valid_phone(phone):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid phone number</b>\n\n"
            "Pass an E.164 or national number like <code>+14155552671</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Looking up phone</b> <code>{html.escape(phone)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_phone, phone, timeout=_TIMEOUTS["phone"])
        await msg.edit_text(
            _format_report(report, "\U0001f4f1 <b>Phone OSINT</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(phone, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(phone, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
