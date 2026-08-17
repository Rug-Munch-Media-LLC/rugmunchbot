"""/track <wallet> — wallet entity/sanction/scam attribution."""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.osint._common import _TIMEOUTS
from app.domains.telegram.rugmunchbot.handlers.osint._format import _error_text, _format_report, _unavailable_text
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.osint import OsintNotAvailable
from app.osint.wallet import lookup_wallet

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_track(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/track <wallet> — wallet entity/sanction/scam attribution."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f50d <b>Wallet Track</b>\n\n"
            "Usage: /track <code>address</code>\n\n"
            "Examples:\n"
            "  /track <code>0xBE0eB53FC46b790099138e3d32C721856d41e865</code>\n"
            "  /track <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>\n\n"
            "Entity labels, exchange attribution, scam reports and sanctions.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = ctx.args[0].strip()
    if len(address) < 20 or any(c.isspace() for c in address):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid wallet address</b>\n\n"
            "Pass a full EVM <code>0x\u2026</code> or Solana base58 address.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Attributing wallet</b> <code>{html.escape(address)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_wallet, address, timeout=_TIMEOUTS["wallet"])
        await msg.edit_text(
            _format_report(report, "\U0001f50d <b>Wallet Track</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(address, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(address, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
