"""/investigate <domain> — WHOIS/DNS/subdomains/SSL/archive summary.

Routes domain-looking args to the OSINT module, otherwise falls through to the
Pro-tier deep investigation (``handlers.investigate.cmd_investigate``).
"""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.investigate import cmd_investigate as _pro_investigate
from app.domains.telegram.rugmunchbot.handlers.osint._common import _TIMEOUTS, _looks_like_domain
from app.domains.telegram.rugmunchbot.handlers.osint._format import _error_text, _format_report, _unavailable_text
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.osint import OsintNotAvailable
from app.osint.domain import lookup_domain

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def _cmd_domain(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/investigate <domain> — WHOIS/DNS/subdomains/SSL/archive summary."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f575\ufe0f <b>Domain OSINT</b>\n\n"
            "Usage: /investigate <code>domain</code>\n\n"
            "Example: /investigate <code>scam-example.com</code>\n\n"
            "WHOIS, DNS, subdomains, SSL certificate and web-archive summary.\n"
            "Pass a token <code>address</code> for the deep investigation report (Pro).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    domain = ctx.args[0].strip()
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Investigating domain</b> <code>{html.escape(domain)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    try:
        report = await asyncio.to_thread(lookup_domain, domain, timeout=_TIMEOUTS["domain"])
        await msg.edit_text(
            _format_report(report, "\U0001f575\ufe0f <b>Domain OSINT</b>"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OsintNotAvailable as exc:
        await msg.edit_text(_unavailable_text(domain, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())
    except (ValueError, TimeoutError, OSError) as exc:
        await msg.edit_text(_error_text(domain, exc), parse_mode=ParseMode.HTML, reply_markup=back_kb())


async def cmd_investigate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/investigate — routes domain-looking args to OSINT, else the Pro deep report.

    Keeps the pre-existing Pro-tier token investigation intact: addresses and
    symbols fall through to ``handlers.investigate.cmd_investigate``.
    """
    if ctx.args and _looks_like_domain(ctx.args[0]):
        await _cmd_domain(update, ctx)
        return
    await _pro_investigate(update, ctx)
