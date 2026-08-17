#!/usr/bin/env python3
"""
RugMunchBot -- Account & billing command handlers.

Commands: account, pricing, topup, buystars, scamschool, refer
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BOT_USERNAME,
    SUPPORT_EMAIL,
    TIERS,
    WEBSITE_URL,
)
from app.domains.telegram.rugmunchbot.formatters import (
    BETA_BANNER,
    BETA_PAYMENTS_LINE,
    BETA_STATUS_LINE,
    footer_links,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    buy_stars_kb,
    pricing_kb,
    scamschool_kb,
    topup_kb,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def cmd_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's account dashboard: tier, weekly usage, balances."""
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    tier = db.get_user_tier(user.id)
    tc = TIERS.get(tier, TIERS["free"])
    scans, ai = db.get_weekly_usage(user.id)
    stats = db.get_user_stats(user.id)
    tier_badge = f"{tc['emoji']} {tc['name']}"
    if u.get("tier_expires") and u["tier_expires"] > 0:
        exp = datetime.fromtimestamp(u["tier_expires"], tz=UTC).strftime("%b %d, %Y")
        tier_badge += f" (expires {exp})"

    nudge = ""
    if tier == "free":
        # FREE PUBLIC BETA: no upgrade pressure — every feature is unlocked
        # while we test; the optional Stars deep-scan pack is the only paid
        # surface. Billing stays one tap away for the launch.
        nudge = (
            f"\n{thin_sep()}\n"
            f"{BETA_STATUS_LINE}\n"
            f"\u2b50 The \U0001f52c <b>Deep Scan</b> button costs "
            f"250\u2605 (Telegram Stars); everything else is free.\n"
            f"\U0001f4c5 <b>Premium plans coming soon</b> \u2014 "
            f"beta users get launch discounts."
        )

    text = (
        f"\U0001f464 <b>Account Dashboard</b>\n{sep()}\n"
        f"<b>User:</b> @{user.username or user.first_name}\n"
        f"<b>Tier:</b> {tier_badge}\n\n"
        f"\U0001f4ca <b>This Week</b>\n{thin_sep()}\n"
        f"\U0001f50d Scans: <b>{scans}/{tc.get('scans_per_week', 25)}</b>\n"
        f"\U0001f916 AI Messages: <b>{ai}/{tc.get('ai_msgs_per_week', 15)}</b>\n\n"
        f"\U0001f381 <b>Bonus Balance</b>\n{thin_sep()}\n"
        f"\U0001f52c Extra Scans: {u.get('bonus_scans', 0)}\n"
        f"\U0001f916 Extra AI Msgs: {u.get('bonus_ai_msgs', 0)}\n"
        f"\u2b50 Stars: {u.get('stars_balance', 0)}\n\n"
        f"\U0001f4c8 <b>All-Time</b>\n{thin_sep()}\n"
        f"Total Scans: {u.get('total_scans', 0)}\n"
        f"Watchlist: {stats.get('watchlist_count', 0)} tokens\n"
        f"{nudge}"
        f"{footer_links()}"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\u2b50 Plans & Pricing", callback_data="menu_pricing"),
                InlineKeyboardButton("\U0001f50b Top Up", callback_data="menu_topup"),
            ],
            [InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)  # type: ignore[union-attr]  # PTB None-able fields


async def cmd_pricing(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the Stars-based premium pricing plans and current balance."""
    user = update.effective_user
    balance = 0
    if user:
        u = db.get_or_create_user(user.id, user.username, user.first_name)
        balance = u.get("stars_balance", 0)
    d = thin_sep()
    text = f"\u2b50 <b>Premium \u2014 pay with Stars</b>\n{sep()}\n\n"
    text += f"{BETA_BANNER}\n\n"
    text += f"Your balance: \u2b50{balance:,}\n\n"
    text += (
        "\U0001f4b0 <b>Free (core)</b> \u2014 /scan, chat, trending, scam school\n"
        f"{d}\n"
        "\U0001f6f0\ufe0f <b>Monitor</b> \u2014 \u2b50250 / 30 days\n"
        "  \u2022 Live LP, whale + deployer wallet moves\n"
        "  \u2022 Tax / ownership / mint change alerts\n"
        f"{d}\n"
        "\U0001f52c <b>Deep Scan</b> \u2014 \u2b50250 / token\n"
        "  \u2022 Full forensics: rugmap, clusters, dev history\n"
        f"{d}\n"
    )
    for key, t in TIERS.items():
        if key == "free":
            continue
        text += f"{t['emoji']} <b>{t['name']}</b> \u2014 {t['price_stars']:,}\u2605 / 30 days\n"
        for f in t["features"][:3]:
            text += f"  \u2022 {f}\n"
        text += "\n"
    text += (
        f"{sep()}\n"
        f"\U0001f4b3 Pay with Telegram Stars (250\u2605 \u2248 $2.50)\n"
        f"\U0001f504 Tier limits reset Monday 00:00 UTC\n\n"
        f'\U0001f310 <a href="{WEBSITE_URL}/pricing">Compare plans on rugmunch.io</a>\n'
        f"\U0001f4e7 Enterprise: {SUPPORT_EMAIL}"
        f"{footer_links()}"
    )
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text, parse_mode=ParseMode.HTML, reply_markup=pricing_kb(), disable_web_page_preview=True
    )


async def cmd_topup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the bonus-usage top-up menu (buy extra scans / AI messages)."""
    user = update.effective_user
    balance = 0
    if user:
        u = db.get_or_create_user(user.id, user.username, user.first_name)
        balance = u.get("stars_balance", 0)
    text = (
        f"\U0001f50b <b>Top Up - Buy Extra Usage</b>\n{sep()}\n\n"
        f"Your balance: \u2b50{balance:,}\n\n"
        f"Ran out of weekly scans? Buy bonus usage!\n\n"
        f"{BETA_PAYMENTS_LINE}\n\n"
        f"\u2705 <b>No expiry</b> - carries over forever\n"
        f"\u2705 <b>Stackable</b> - buy multiple times\n"
        f"\u2705 <b>Instant</b> - available immediately\n\n"
        f"\U0001f4b3 Pay with Telegram Stars \u2b50"
        f"{footer_links()}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=topup_kb())  # type: ignore[union-attr]  # PTB None-able fields


async def cmd_buystars(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Buy Telegram Stars - purchase Stars to spend on scans, subs, etc."""
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    text = (
        f"\u2b50 <b>Buy Telegram Stars</b>\n{sep()}\n\n"
        f"Current balance: \u2b50{u.get('stars_balance', 0):,}\n\n"
        f"Purchase Stars via Telegram and spend them on scans, "
        f"subscriptions, and premium features!\n\n"
        f"{BETA_PAYMENTS_LINE}\n\n"
        f"Select a pack below:"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=buy_stars_kb())  # type: ignore[union-attr]  # PTB None-able fields


async def cmd_scamschool(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the scam-education topic menu."""
    text = (
        f"\U0001f4da <b>Scam School</b>\n{sep()}\n\n"
        f"Learn how to spot crypto scams <b>before</b> you get rugged.\n\n"
        f'Free education from <a href="{WEBSITE_URL}">Rug Munch Intelligence</a>.\n\n'
        f"Select a topic below \U0001f447"
        f"{footer_links()}"
    )
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text, parse_mode=ParseMode.HTML, reply_markup=scamschool_kb(), disable_web_page_preview=True
    )


async def cmd_refer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the referral link and earned-bonus stats."""
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)
    ref = u.get("referral_code", "UNKNOWN")
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{ref}"
    conn = db.get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM users WHERE referred_by = ?", (ref,)).fetchone()["c"]
    conn.close()
    milestones = {5: "\U0001f949", 10: "\U0001f948", 25: "\U0001f947", 50: "\U0001f48e", 100: "\U0001f451"}
    next_ms = next((k for k in sorted(milestones.keys()) if k > count), None)
    ms_text = (
        f"\n\U0001f3af Next milestone: {milestones[next_ms]} {next_ms} referrals"
        if next_ms
        else "\n\U0001f451 You've hit all milestones!"
    )
    text = (
        f"\U0001f91d <b>Refer & Earn</b>\n{sep()}\n\n"
        f"Invite friends and <b>both get 5 bonus scans!</b>\n\n"
        f"\U0001f517 <b>Your Link:</b>\n<code>{link}</code>\n\n"
        f"\U0001f4ca <b>Your Stats:</b>\n"
        f"  Friends Referred: <b>{count}</b>\n"
        f"  Bonus Earned: <b>{count * 5}</b> scans{ms_text}\n\n"
        f"<b>How it works:</b>\n"
        f"  1. Share your referral link\n"
        f"  2. Friend starts the bot via your link\n"
        f"  3. Both of you get 5 bonus scans instantly\n"
        f"  4. No limits - refer as many as you want!"
        f"{footer_links()}"
    )
    share_text = quote(
        f"Check out Rug Munch Intelligence - the best crypto scam detector on Telegram! \U0001f6e1\ufe0f\n\n{link}"
    )
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "\U0001f4e4 Share Link", url=f"https://t.me/share/url?url={link}&text={share_text}"
                    )
                ],
                [InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL)],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        ),
        disable_web_page_preview=True,
    )
