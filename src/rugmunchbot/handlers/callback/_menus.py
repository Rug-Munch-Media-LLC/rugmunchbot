#!/usr/bin/env python3
"""
RugMunchBot -- callback menu branches.

Menu navigation callbacks (``menu_*`` exact matches), the open-* external
links, plus the account/watchlist/refer/buy-stars cards. Split out of the
historic single-function ``handle_callback`` chain (2026-08-16). All branches
here are *exact* matches so there is no prefix overlap against the other
callback modules; ``handle()`` returns True only when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BOT_USERNAME,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    WEBSITE_URL,
)
from app.domains.telegram.rugmunchbot.formatters import (
    is_owner,
    thin_sep,
    welcome_card_text,
)
from app.domains.telegram.rugmunchbot.handlers._help_text import MENU_TEXT
from app.domains.telegram.rugmunchbot.handlers.callback._admin import _deny_callback_attempt
from app.domains.telegram.rugmunchbot.keyboards import (
    admin_kb,
    back_kb,
    buy_stars_kb,
    category_account_kb,
    category_ai_kb,
    category_alerts_kb,
    category_charts_kb,
    category_help_kb,
    category_intel_kb,
    category_market_kb,
    category_portfolio_kb,
    category_rugmaps_kb,
    category_scan_kb,
    category_security_kb,
    category_wallet_kb,
    menu_kb,
    pricing_kb,
    scamschool_kb,
    token_buttons,
    topup_kb,
    welcome_menu_kb,
)

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def _edit(
    query: Any,
    text: str,
    *,
    kb: Any = None,
    disable_web_page_preview: bool = False,
) -> None:
    """Edit a callback message as HTML (single send-style helper)."""
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=disable_web_page_preview,
    )


async def handle(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    query: Any,
    user: Any,
    data: str,
) -> bool:
    """Handle menu/account/watchlist callbacks. True when a branch matched."""
    if data == "menu_main":
        await _edit(
            query,
            welcome_card_text(),
            kb=welcome_menu_kb(user.id),
            disable_web_page_preview=True,
        )
    elif data == "menu_full":
        # First-run "More" button → the full /menu capability atlas (MENU_TEXT).
        await _edit(query, MENU_TEXT, kb=menu_kb(), disable_web_page_preview=True)
    elif data == "menu_cat_admin":
        # Owner-only admin sub-menu — visible via the welcome menu Admin button
        # (which itself is only rendered for the owner).
        if not is_owner(user.id):
            await query.answer("\U0001f512 Owner only.", show_alert=True)
            await _deny_callback_attempt(update, ctx, "menu_cat_admin")
            return True
        await _edit(
            query,
            "\U0001f6e1\ufe0f <b>Admin Panel</b>\n\n"
            "\U0001f4ca /admin \u2014 Dashboard & stats\n"
            "\U0001f4e2 /admin_broadcast \u2014 Send to all users\n"
            "\U0001f465 /admin_user \u2014 Look up a user\n"
            "\U0001f3af /admin_set_tier \u2014 Change user tier\n"
            "\U0001f6ab /admin_ban \u2014 Ban a user\n"
            "\U0001f4dd /admin_logs \u2014 Audit log\n"
            "\U0001f6e1\ufe0f /admin_toggle \u2014 Bot ON/OFF\n"
            "\U0001f4ac /admin_toggle_chat \u2014 Chat ON/OFF\n\n"
            "<i>Admin buttons stay pinned below the input box \u2014 "
            "tap any message and use them anytime.</i>",
            kb=admin_kb(),
        )
    elif data == "menu_cat_scan":
        await _edit(
            query,
            "\U0001f50d <b>Scan</b>\n\n"
            "Security audits across 25+ chains \u2014 Quick Scan, full "
            "forensic Deep Scan, AEGIS 9-module analysis, rug checks, "
            "deployer fingerprint and hype/compare tools.",
            kb=category_scan_kb(),
        )
    elif data == "menu_cat_wallet":
        await _edit(
            query,
            "\U0001f45b <b>Wallet</b>\n\n"
            "Wallet forensics, portfolio holdings, watchlist tracking and "
            "trade simulation \u2014 all wallet tools in one place. Price "
            "alerts live under the dedicated Alerts section.",
            kb=category_wallet_kb(),
        )
    elif data == "menu_cat_alerts":
        await _edit(
            query,
            "\U0001f514 <b>Alerts</b>\n\n"
            "Price & activity alerts, watchlist tracking, whale movement "
            "alerts and custom webhook notifications \u2014 stay on top of "
            "your tokens without staring at charts.",
            kb=category_alerts_kb(),
        )
    elif data == "menu_cat_intel":
        await _edit(
            query,
            "\U0001f9e0 <b>Intelligence</b>\n\n"
            "Market overview, trending, movers, news, sentiment, social "
            "signals, RAG deep research, deep answers and freestyle AI "
            "chat \u2014 knowledge on demand.",
            kb=category_intel_kb(),
        )
    elif data == "menu_cat_charts":
        await _edit(
            query,
            "\U0001f4c9 <b>RugCharts</b>\n\n"
            "Price chart images, technical analysis and market data for any "
            "token. Bubble maps live under RugMaps.",
            kb=category_charts_kb(),
        )
    elif data == "menu_cat_rugmaps":
        await _edit(
            query,
            "\U0001f5fa\ufe0f <b>RugMaps</b>\n\n"
            "Bubble-map images of wallet clusters, fund flows, dev wallets "
            "and smart money \u2014 plus top-holder concentration analysis.",
            kb=category_rugmaps_kb(),
        )
    elif data == "menu_cat_security":
        await _edit(
            query,
            "\U0001f6e1\ufe0f <b>Security & Education</b>\n\n"
            "Rug pull checklists, scam education, deep investigations and "
            "reporting suspicious addresses to the Rug Munch team.",
            kb=category_security_kb(),
        )
    elif data == "menu_cat_market":
        await _edit(
            query,
            "\U0001f4c8 <b>Market</b>\n\n"
            "Market overview, trending, movers, new launches, whale "
            "activity, charts, prices, TA, DEX liquidity, holders, smart "
            "money and fund tracing \u2014 real-time market data.",
            kb=category_market_kb(),
        )
    elif data == "menu_cat_ai":
        await _edit(
            query,
            "\U0001f916 <b>AI Chat</b>\n\n"
            "Talk to me about tokens, wallets, markets, DeFi, crypto news and "
            "coding \u2014 powered by RAG + LLM.",
            kb=category_ai_kb(),
        )
    elif data == "menu_cat_chat":
        # Direct entry to AI chat — show welcome card with examples
        from app.domains.telegram.rugmunchbot.handlers.chat import CHAT_WELCOME

        await _edit(query, CHAT_WELCOME, kb=back_kb())
    elif data == "menu_cat_portfolio":
        await _edit(
            query,
            "\U0001f4c1 <b>Portfolio</b>\n\nTrack your wallet holdings, watchlist, alerts and trade simulations.",
            kb=category_portfolio_kb(),
        )
    elif data == "menu_cat_account":
        await _edit(
            query,
            "\U0001f464 <b>Account</b>\n\nYour usage dashboard, subscription plans, referrals and scam education.",
            kb=category_account_kb(),
        )
    elif data == "menu_cat_help":
        await _edit(
            query,
            "\u2139\ufe0f <b>Help & About</b>\n\n"
            "Full command reference, community links and the Rug Munch mission: "
            "make crypto safer for everyone.",
            kb=category_help_kb(),
        )
    elif data in ("open_channel", "open_group", "open_website"):
        url = {
            "open_channel": TELEGRAM_CHANNEL,
            "open_group": TELEGRAM_GROUP,
            "open_website": WEBSITE_URL,
        }[data]
        await _edit(
            query,
            f'\U0001f310 Open: <a href="{url}">{url}</a>',
            kb=category_help_kb(),
            disable_web_page_preview=True,
        )
    elif data == "menu_scan":
        await _edit(
            query,
            "\U0001f50d <b>Scan a Token</b>\n\nSend me a contract address:\n<code>0x6982...1933</code>\n\nOr use: /scan <code>address</code>",
            kb=back_kb(),
        )
    elif data == "menu_wallet":
        await _edit(
            query,
            "\U0001f9db <b>Wallet Check</b>\n\nSend a wallet address or use:\n/wallet <code>address</code>",
            kb=back_kb(),
        )
    elif data == "menu_ta":
        await _edit(
            query,
            "\U0001f4ca <b>Technical Analysis</b>\n\nUsage: /ta <code>address</code>\n\nAnalyzes momentum, volume, buy/sell pressure.",
            kb=back_kb(),
        )
    elif data == "menu_alerts":
        await _edit(
            query,
            "\U0001f514 <b>Price Alerts</b>\n\nUse /alerts to manage.\n\n<i>Scout+ tiers only.</i>",
            kb=back_kb(),
        )
    elif data == "menu_account":
        tier = db.get_user_tier(user.id)
        tc = TIERS.get(tier, TIERS["free"])
        scans, ai = db.get_weekly_usage(user.id)
        u = db.get_user_stats(user.id)
        text = (
            f"\U0001f464 <b>Account</b>\n{thin_sep()}\n"
            f"Tier: {tc['emoji']} {tc['name']}\n"
            f"Scans: {scans}/{tc.get('scans_per_week', 25)}\n"
            f"AI: {ai}/{tc.get('ai_msgs_per_week', 15)}\n"
            f"Bonus Scans: {u.get('bonus_scans', 0)}\n"
            f"Bonus AI: {u.get('bonus_ai_msgs', 0)}\n"
            f"Stars: {u.get('stars_balance', 0)}\u2b50\n"
            f"Watchlist: {u.get('watchlist_count', 0)}"
        )
        await _edit(
            query,
            text,
            kb=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("\u2b50 Upgrade", callback_data="menu_pricing"),
                        InlineKeyboardButton("\U0001f50b Top Up", callback_data="menu_topup"),
                    ],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            ),
        )
    elif data == "menu_pricing":
        await _edit(
            query,
            "\u2b50 <b>Premium — pay with Stars</b>\n\n"
            "\U0001f4b0 <b>Core /scan + chat are free.</b>\n\n"
            "\U0001f6f0\ufe0f <b>Monitor</b> (250\u2605/30d) \u2014 live LP/whale + "
            "tax/ownership/mint change alerts\n"
            "\U0001f52c <b>Deep Scan</b> (250\u2605) \u2014 full forensics on demand\n"
            "\U0001f451 <b>Tier upgrades</b> \u2014 higher scan/AI weekly limits\n\n"
            "Start a monitor with /monitor <code>address</code>, or pick a plan:",
            kb=pricing_kb(),
        )
    elif data == "menu_monitor_howto":
        await _edit(
            query,
            "\U0001f6f0\ufe0f <b>Token Monitor (250\u2605 / 30 days)</b>\n\n"
            "Watch a token for rug-relevant changes and get DM alerts:\n"
            "\u2022 LP add/remove & lock changes\n"
            "\u2022 Whale + deployer wallet moves\n"
            "\u2022 Tax / ownership / mint changes\n"
            "\u2022 Price swings\n\n"
            "How to start:\n"
            "  /monitor <code>address</code> [chain]\n\n"
            "You'll get a Telegram Stars invoice \u2014 pay once, monitor for 30 days.\n"
            "Manage: /monitors \u00b7 Stop: /unmonitor <code>address</code>",
            kb=back_kb(),
        )
    elif data == "menu_topup":
        await _edit(
            query,
            "\U0001f50b <b>Top Up</b>\n\nBuy extra usage (no expiry):",
            kb=topup_kb(),
        )
    elif data == "menu_scamschool":
        await _edit(
            query,
            "\U0001f4da <b>Scam School</b>\n\nSelect a topic:",
            kb=scamschool_kb(),
        )
    elif data == "menu_trending":
        await _edit(query, "\U0001f525 <b>Fetching trending...</b>")
        from app.domains.telegram.rugmunchbot.handlers.market import render_trending

        trend_text, trend_kb = await render_trending("solana", limit=8, window="24h")
        if trend_text is None or trend_kb is None:
            await _edit(
                query,
                "\u274c Could not fetch trending data.",
                kb=back_kb(),
            )
            return True
        await _edit(
            query,
            trend_text,
            kb=trend_kb,
            disable_web_page_preview=True,
        )
    elif data == "menu_watchlist":
        items = db.get_watchlist(user.id)
        if not items:
            await _edit(
                query,
                "\U0001f440 Watchlist is empty.\nUse /watch <code>address</code>",
                kb=back_kb(),
            )
        else:
            text = "\U0001f440 <b>Watchlist</b>\n\n" + "\n".join(
                f"\u2022 {w.get('symbol', '???')} <code>{w['token_address']}</code>" for w in items
            )
            rows: list[list[InlineKeyboardButton]] = []
            for w in items:
                rows.append(token_buttons(w["token_address"], w.get("chain") or "solana"))
            rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
            await _edit(query, text, kb=InlineKeyboardMarkup(rows))
    elif data == "noop":
        await query.answer()
    elif data == "menu_refer":
        u = db.get_or_create_user(user.id)
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{u.get('referral_code', '')}"
        await _edit(
            query,
            f"\U0001f91d <b>Refer Friends</b>\n\nShare your link:\n<code>{link}</code>\n\nBoth get 5 bonus scans!",
            kb=back_kb(),
        )
    elif data == "menu_buystars":
        u = db.get_or_create_user(user.id, user.username, user.first_name)
        await _edit(
            query,
            f"\u2b50 <b>Buy Stars</b>\n\n"
            f"Your balance: \u2b50{u.get('stars_balance', 0):,}\n\n"
            f"Purchase Stars and spend them on scans, subscriptions, "
            f"and premium features!\n\n"
            f"Select a pack below \U0001f447",
            kb=buy_stars_kb(),
        )
    else:
        return False
    return True
