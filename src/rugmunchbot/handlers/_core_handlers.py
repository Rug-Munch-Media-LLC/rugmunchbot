#!/usr/bin/env python3
"""
RugMunchBot -- core welcome/help command handlers (degodded 2026-08-14).

Extracted from ``handlers/scanning.py``: ``cmd_start``, ``cmd_help``,
``cmd_faq``. Re-exported by ``handlers.scanning`` so existing importers
(including the lazy ``chat_ai`` imports of ``cmd_start``) keep working
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.daily_context import get_market_section
from app.domains.telegram.rugmunchbot.formatters import faq_text, is_owner, welcome_card_text
from app.domains.telegram.rugmunchbot.handlers._engagement import channels_menu_text, rotating_cta_for
from app.domains.telegram.rugmunchbot.handlers._help_text import HELP_TEXT, MENU_TEXT
from app.domains.telegram.rugmunchbot.handlers.onboarding import is_first_run, send_onboarding_welcome
from app.domains.telegram.rugmunchbot.keyboards import (
    daily_hook_kb,
    menu_kb,
    merge_menu_kb,
    owner_admin_reply_kb,
    welcome_menu_kb,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — upsert the user, handle referral codes, send the welcome menu."""
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)

    # Referral handling
    if ctx.args and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        if ref_code != u.get("referral_code") and not u.get("referred_by"):
            conn = db.get_db()
            referrer = conn.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,)).fetchone()
            if referrer:
                conn.execute(
                    "UPDATE users SET referred_by = ?, bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (ref_code, user.id),
                )
                conn.execute(
                    "UPDATE users SET bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (referrer["user_id"],),
                )
                conn.commit()
            conn.close()

    # First-run onboarding: punchy welcome + quick-start buttons. Returning
    # users get the standard welcome card with the daily hook attached.
    if is_first_run(u):
        await send_onboarding_welcome(update, ctx)
    else:
        text = welcome_card_text()

        # Live daily market awareness: compact "Today's Market" section
        # (Redis-cached 5 min, fail-open — /start never blocks on market data).
        market_section = await get_market_section()
        if market_section:
            text += f"\n\n{market_section}"

        # Channel shill: compact "📢 Our Channels" block (public handles only).
        text += f"\n\n{channels_menu_text()}"

        # Returning-user engagement: one rotating, non-spammy footer CTA line
        # (deterministic per user+day — never more than one line).
        text += f"\n{rotating_cta_for(user.id)}"

        # Owner-only admin section — visible only to @cryptorugmunch.
        if is_owner(user.id):
            text += (
                "\n\n\U0001f6e1\ufe0f <b>ADMIN</b>\n"
                "\u2514 /admin \u2014 Dashboard & stats\n"
                "\u2514 /admin_broadcast \u2014 Send to all users\n"
                "\u2514 /admin_user \u2014 Look up a user\n"
                "\u2514 /admin_toggle \u2014 Bot ON/OFF\n"
                "\u2514 \U0001f447 Admin buttons are pinned below the input box"
            )
        kb = welcome_menu_kb(user.id)
        # Daily hook: one-tap "what's hot right now" + market pulse for
        # returning users — instant value, zero new callback handlers.
        kb = merge_menu_kb(kb, daily_hook_kb())
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)  # type: ignore[union-attr]  # PTB None-able fields

    # v7: attach the persistent admin reply keyboard for the owner in DMs so
    # admin commands stay one tap away on every message.
    if is_owner(user.id) and update.effective_chat and update.effective_chat.type == "private":
        from app.domains.telegram.rugmunchbot.handlers.message import (
            admin_kb_mark_sent,
            admin_kb_reset,
        )

        admin_kb_reset(user.id)
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f6e1\ufe0f <b>Admin console</b>\n"
            "Tap a button below for quick admin actions \u2014 they stay pinned here.",
            parse_mode=ParseMode.HTML,
            reply_markup=owner_admin_reply_kb(),
        )
        admin_kb_mark_sent(user.id)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Full command reference (v10) — grouped by category:
    Scan & Security / Wallet & Tracing / Market & Analysis / News & Alerts /
    Chat & Learn / Premium, plus Account & Extras."""
    user = update.effective_user
    kb = welcome_menu_kb(user.id if user else None)
    text = HELP_TEXT
    # Channel shill: compact "📢 Our Channels" block (public handles only).
    text += f"\n\n{channels_menu_text()}"
    # Engagement polish: one rotating, non-spammy "what next" CTA line.
    if user is not None:
        text += f"\n\n{rotating_cta_for(user.id)}"
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/menu (v13) — capability atlas grouped by intent.

    Full grouped list of every registered command (Scan & Safety / Prices &
    Charts / Portfolio & Wallets / News & Intel / Education / Account &
    Community), each with a one-line description + example usage, plus a
    quick-action inline keyboard built from existing wired callbacks.
    """
    user = update.effective_user
    if user is None:
        return
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        MENU_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=menu_kb(),
        disable_web_page_preview=True,
    )


async def cmd_faq(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Lightweight FAQ — plain-language answers to common questions
    (what can you scan / is it free / what is RMI / how accurate)."""
    user = update.effective_user
    kb = welcome_menu_kb(user.id if user else None)
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        faq_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
