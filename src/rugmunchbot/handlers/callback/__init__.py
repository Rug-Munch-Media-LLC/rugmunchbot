"""RugMunchBot — Callback query dispatcher (thin OSS port).

The live module is a 914-line god file that mixes menu navigation, admin
dispatch, inline renderers and private Telegram-Stars billing. The OSS
scaffold already split the portable pieces into submodules (all ported):

  * ``_admin.py``     — owner-only ``admin_*`` callback dispatch + stats/log
                        cards + the non-owner deny helper.
  * ``_inline.py``    — live inline renderers behind the ``run_*`` refresh
                        buttons (market/movers/launches/whales/trending/
                        news/sentiment).
  * ``_run_cards.py`` — the static ``_RUN_CARDS`` usage-card map.

This ``__init__`` keeps the live namespace intact (re-exports the submodule
helpers so ``from ...handlers.callback import X`` resolves unchanged), ports
the small ``_dispatch_run_cmd`` dispatcher (the split docstring says it stays
here), and ships a ``handle_callback`` that covers the OSS-portable surface.
Private billing branches (Stars invoices, paid tiers, top-ups, referral
attribution) fail open with a neutral notice instead of shipping pricing —
see the ``# TODO(port)`` / ``[REVIEW]`` markers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import (
    BOT_USERNAME,
    SCAM_SCHOOL_TOPICS,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    WEBSITE_URL,
)
from rugmunchbot.formatters import footer_links, is_owner, thin_sep, welcome_card_text
from rugmunchbot.handlers.callback._admin import (
    _deny_callback_attempt,
    _dispatch_admin_cb,
)
from rugmunchbot.handlers.callback._inline import (
    _inline_market_data,
    _inline_news,
    _inline_sentiment,
    _inline_trending,
)
from rugmunchbot.handlers.callback._run_cards import _RUN_CARDS
from rugmunchbot.keyboards import (
    admin_kb,
    back_kb,
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
    scamschool_kb,
    token_buttons,
    welcome_menu_kb,
)
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

__all__ = [
    "_RUN_CARDS",
    "_deny_callback_attempt",
    "_dispatch_admin_cb",
    "_dispatch_run_cmd",
    "_inline_market_data",
    "_inline_news",
    "_inline_sentiment",
    "_inline_trending",
    "handle_callback",
]


async def _billing_unavailable(query: CallbackQuery, title: str) -> None:
    """Fail-open answer for private billing surfaces (Stars/paid tiers).

    Billing is the operator's business (checklist §8): OSS ships a single
    free tier with env quotas. Tapping a pricing/top-up/Stars button answers
    with a neutral notice instead of shipping private pricing or crashing.
    """
    logger.info("billing_surface_unavailable", surface=title)
    await query.edit_message_text(
        f"{title}\n\n"
        "\u2b50 Subscription &amp; billing are not available in this build \u2014 "
        "the free tier is always active.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data
    if data is None:
        return
    user = query.from_user
    db.get_or_create_user(user.id, user.username, user.first_name)

    if data == "menu_main":
        await query.edit_message_text(
            welcome_card_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=welcome_menu_kb(user.id),
            disable_web_page_preview=True,
        )
    elif data == "menu_cat_admin":
        # Owner-only admin sub-menu — visible via the welcome menu Admin button
        # (which itself is only rendered for the owner).
        if not is_owner(user.id):
            await query.answer("\U0001f512 Owner only.", show_alert=True)
            await _deny_callback_attempt(update, ctx, "menu_cat_admin")
            return
        await query.edit_message_text(
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
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data == "menu_cat_scan":
        await query.edit_message_text(
            "\U0001f50d <b>Scan</b>\n\n"
            "Security audits across 25+ chains \u2014 Quick Scan, full "
            "forensic Deep Scan, AEGIS 9-module analysis, rug checks, "
            "deployer fingerprint and hype/compare tools.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_scan_kb(),
        )
    elif data == "menu_cat_wallet":
        await query.edit_message_text(
            "\U0001f45b <b>Wallet</b>\n\n"
            "Wallet forensics, portfolio holdings, watchlist tracking and "
            "trade simulation \u2014 all wallet tools in one place. Price "
            "alerts live under the dedicated Alerts section.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_wallet_kb(),
        )
    elif data == "menu_cat_alerts":
        await query.edit_message_text(
            "\U0001f514 <b>Alerts</b>\n\n"
            "Price & activity alerts, watchlist tracking, whale movement "
            "alerts and custom webhook notifications \u2014 stay on top of "
            "your tokens without staring at charts.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_alerts_kb(),
        )
    elif data == "menu_cat_intel":
        await query.edit_message_text(
            "\U0001f9e0 <b>Intelligence</b>\n\n"
            "Market overview, trending, movers, news, sentiment, social "
            "signals, RAG deep research, deep answers and freestyle AI "
            "chat \u2014 knowledge on demand.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_intel_kb(),
        )
    elif data == "menu_cat_charts":
        await query.edit_message_text(
            "\U0001f4c9 <b>RugCharts</b>\n\n"
            "Price chart images, technical analysis and market data for any "
            "token. Bubble maps live under RugMaps.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_charts_kb(),
        )
    elif data == "menu_cat_rugmaps":
        await query.edit_message_text(
            "\U0001f5fa\ufe0f <b>RugMaps</b>\n\n"
            "Bubble-map images of wallet clusters, fund flows, dev wallets "
            "and smart money \u2014 plus top-holder concentration analysis.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_rugmaps_kb(),
        )
    elif data == "menu_cat_security":
        await query.edit_message_text(
            "\U0001f6e1\ufe0f <b>Security & Education</b>\n\n"
            "Rug pull checklists, scam education, deep investigations and "
            "reporting suspicious addresses to the Rug Munch team.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_security_kb(),
        )
    elif data == "menu_cat_market":
        await query.edit_message_text(
            "\U0001f4c8 <b>Market</b>\n\n"
            "Market overview, trending, movers, new launches, whale "
            "activity, charts, prices, TA, DEX liquidity, holders, smart "
            "money and fund tracing \u2014 real-time market data.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_market_kb(),
        )
    elif data == "menu_cat_ai":
        await query.edit_message_text(
            "\U0001f916 <b>AI Chat</b>\n\n"
            "Talk to me about tokens, wallets, markets, DeFi, crypto news and "
            "coding \u2014 powered by RAG + LLM.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_ai_kb(),
        )
    elif data == "menu_cat_chat":
        # Direct entry to AI chat — show welcome card with examples
        from rugmunchbot.handlers.chat import CHAT_WELCOME

        await query.edit_message_text(
            CHAT_WELCOME,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_cat_portfolio":
        await query.edit_message_text(
            "\U0001f4c1 <b>Portfolio</b>\n\nTrack your wallet holdings, watchlist, alerts and trade simulations.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_portfolio_kb(),
        )
    elif data == "menu_cat_account":
        await query.edit_message_text(
            "\U0001f464 <b>Account</b>\n\nYour usage dashboard, subscription plans, referrals and scam education.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_account_kb(),
        )
    elif data == "menu_cat_help":
        await query.edit_message_text(
            "\u2139\ufe0f <b>Help & About</b>\n\n"
            "Full command reference, community links and the Rug Munch mission: "
            "make crypto safer for everyone.",
            parse_mode=ParseMode.HTML,
            reply_markup=category_help_kb(),
        )
    elif data in ("open_channel", "open_group", "open_website"):
        url = {
            "open_channel": TELEGRAM_CHANNEL,
            "open_group": TELEGRAM_GROUP,
            "open_website": WEBSITE_URL,
        }[data]
        await query.edit_message_text(
            f'\U0001f310 Open: <a href="{url}">{url}</a>',
            parse_mode=ParseMode.HTML,
            reply_markup=category_help_kb(),
            disable_web_page_preview=True,
        )
    elif data.startswith("run_"):
        await _dispatch_run_cmd(update, ctx, data[4:])
    elif data.startswith("admin_"):
        await _dispatch_admin_cb(update, ctx, data)
    elif data == "menu_scan":
        await query.edit_message_text(
            "\U0001f50d <b>Scan a Token</b>\n\nSend me a contract address:\n<code>0x6982...1933</code>\n\nOr use: /scan <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_wallet":
        await query.edit_message_text(
            "\U0001f9db <b>Wallet Check</b>\n\nSend a wallet address or use:\n/wallet <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_ta":
        await query.edit_message_text(
            "\U0001f4ca <b>Technical Analysis</b>\n\nUsage: /ta <code>address</code>\n\nAnalyzes momentum, volume, buy/sell pressure.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_alerts":
        await query.edit_message_text(
            "\U0001f514 <b>Price Alerts</b>\n\nUse /alerts to manage.\n\n<i>Scout+ tiers only.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_account":
        # TODO(port): the live card also shows the user's Stars balance and
        # Upgrade/Top-Up buttons — private billing (checklist §3, scrub #17).
        # OSS shows the quota card only. [REVIEW]
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
            f"Watchlist: {u.get('watchlist_count', 0)}"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]
            ),
        )
    elif data == "menu_pricing":
        # TODO(port): premium tiers / Stars pricing are private billing —
        # OSS ships a single free tier; operator pricing lands via a
        # BillingProvider (checklist §3, scrub #17). [REVIEW]
        await _billing_unavailable(query, "\u2b50 <b>Upgrade Plan</b>")
    elif data == "menu_topup":
        # TODO(port): Stars top-up packs are private billing. [REVIEW]
        await _billing_unavailable(query, "\U0001f50b <b>Top Up</b>")
    elif data == "menu_scamschool":
        await query.edit_message_text(
            "\U0001f4da <b>Scam School</b>\n\nSelect a topic:",
            parse_mode=ParseMode.HTML,
            reply_markup=scamschool_kb(),
        )
    elif data == "menu_trending":
        await query.edit_message_text(
            "\U0001f525 <b>Fetching trending...</b>", parse_mode=ParseMode.HTML
        )
        from rugmunchbot.handlers.market import render_trending

        trend_text, trend_kb = await render_trending("solana", limit=8, window="24h")
        if trend_text is None or trend_kb is None:
            await query.edit_message_text(
                "\u274c Could not fetch trending data.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        await query.edit_message_text(
            trend_text,
            parse_mode=ParseMode.HTML,
            reply_markup=trend_kb,
            disable_web_page_preview=True,
        )
    elif data == "trending_refresh":
        # Routed to the ported inline renderer (same render_trending path).
        await _inline_trending(update, ctx)
    elif data == "news_refresh":
        # Routed to the ported inline news renderer (backend fetch + fallback).
        await _inline_news(update, ctx)
    elif data.startswith("dismiss_alert_"):
        import contextlib

        with contextlib.suppress(ConnectionError, OSError, TimeoutError):
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                "\u2705 Alert dismissed.",
                parse_mode=ParseMode.HTML,
            )
        await query.answer("Alert dismissed.")
    elif data == "menu_watchlist":
        items = db.get_watchlist(user.id)
        if not items:
            await query.edit_message_text(
                "\U0001f440 Watchlist is empty.\nUse /watch <code>address</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        else:
            text = "\U0001f440 <b>Watchlist</b>\n\n" + "\n".join(
                f"\u2022 {w.get('symbol', '???')} <code>{w['token_address']}</code>" for w in items
            )
            rows: list[list[InlineKeyboardButton]] = []
            for w in items:
                rows.append(token_buttons(w["token_address"], w.get("chain") or "solana"))
            rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
            await query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows)
            )
    elif data == "noop":
        await query.answer()
    elif data == "menu_refer":
        # TODO(port): referral links are revenue attribution (ref codes,
        # bonus-scans grant) — private, scrub #10. Stub fails open. [REVIEW]
        await _billing_unavailable(query, "\U0001f91d <b>Refer Friends</b>")
    elif data.startswith("scam_"):
        topic_key = data[5:]
        topic = SCAM_SCHOOL_TOPICS.get(topic_key)
        if topic:
            await query.edit_message_text(
                topic["content"],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=scamschool_kb(),
            )
    elif data.startswith("scan_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            await query.edit_message_text(
                f"\U0001f50d Use /scan {parts[1]} to run a full scan.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("share_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            # Scrub: the live bot hardcoded the branded username; OSS renders
            # the operator's own BOT_USERNAME env.
            share_text = (
                f"\U0001f50d <b>Token Scan</b>\n\n"
                f"<code>{addr}</code> ({chain.title()})\n\n"
                f"Scan this token with Rug Munch Bot:\n"
                f"https://t.me/{BOT_USERNAME}?start=scan_{addr}_{chain}"
            )
            await query.edit_message_text(
                share_text,
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("holders_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f465 Use /holders {addr} {chain} to view holder analysis.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("trace_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f50d Use /trace {addr} to trace cross-chain fund flows.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("wallet_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f9db Use /wallet {addr} to analyze this wallet.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("chart_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f4c8 <b>Generating chart...</b>\n<code>{addr}</code>",
                parse_mode=ParseMode.HTML,
            )
            from rugmunchbot.commands.chart import cmd_chart

            ctx.args = [addr, chain]
            await cmd_chart(update, ctx)
    elif data.startswith("rugmap_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f5fa\ufe0f <b>Generating RugMap...</b>\n<code>{addr}</code>",
                parse_mode=ParseMode.HTML,
            )
            from rugmunchbot.commands.rugmap import cmd_rugmap

            ctx.args = [addr, chain]
            await cmd_rugmap(update, ctx)
    elif data.startswith("alert_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f514 To set a price alert:\n\n"
                f"  /alert ABOVE {addr} 0.01\n"
                f"  /alert BELOW {addr} 0.005\n"
                f"  /alert CHANGE_5 {addr}\n\n"
                f"Requires Scout+ tier.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("upgrade_"):
        # TODO(port): paid-tier upgrade cards are private billing (scrub #17). [REVIEW]
        await _billing_unavailable(query, "\u2b50 <b>Upgrade</b>")
    elif data == "api_revoke":
        await query.edit_message_text(
            "\U0001f504 Revoke all active API tokens?\n\nThis will immediately invalidate all tokens.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\u2705 Yes, Revoke All", callback_data="api_revoke_confirm"
                        ),
                        InlineKeyboardButton("\U0001f519 Cancel", callback_data="menu_main"),
                    ]
                ]
            ),
        )
    elif data == "api_revoke_confirm":
        conn = db.get_db()
        conn.execute(
            "UPDATE api_tokens SET is_active = 0 WHERE user_id = ? AND is_active = 1",
            (user.id,),
        )
        conn.commit()
        conn.close()
        await query.edit_message_text(
            "\u2705 All API tokens revoked.\n\nGenerate new ones with /api create.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data.startswith("watch_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr, chain = parts[1], parts[2]
            ok = db.add_watchlist(user.id, addr, chain)
            await query.answer(
                "Added to watchlist!" if ok else "Could not add (limit reached?)",
                show_alert=True,
            )
    elif data == "menu_buystars":
        # TODO(port): Telegram Stars packs are private billing (scrub #17). [REVIEW]
        await _billing_unavailable(query, "\u2b50 <b>Buy Stars</b>")
    elif data.startswith("buystars_"):
        # TODO(port): Stars invoice flow is private billing (scrub #17). [REVIEW]
        await _billing_unavailable(query, "\u2b50 <b>Buy Stars</b>")
    elif data.startswith("research_"):
        parts = data.split("_", 2)
        if len(parts) == 2:
            # Topic-based research suggestion
            topic_key = parts[1]
            topic_desc = {
                "honeypot": "honeypot token detection patterns",
                "bundling": "bundle buying detection techniques",
                "wash_trading": "wash trading detection methods",
                "rugpull": "common rug pull strategies",
                "smart_money": "smart money wallet tracking",
                "fresh_wallet": "fresh wallet detection risk assessment",
                "mev": "MEV attacks sandwich detection",
                "pump_dump": "pump and dump scheme detection",
                "liquidity": "liquidity analysis LP verification",
            }.get(topic_key, topic_key)
            await query.edit_message_text(
                "\U0001f50d <b>Research Topic Selected</b>\n\n"
                f"Use /research <code>{topic_desc}</code>\n\n"
                "Or type a custom query:\n"
                "/research <code>your question here</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
        elif len(parts) >= 3:
            addr_s, _chain_s = parts[1], parts[2]
            await query.edit_message_text(
                "\U0001f50d Use /research or /investigate for deep analysis.\n\n"
                f"\U0001f50d /research <code>{addr_s}</code>\n"
                f"\U0001f575\ufe0f /investigate <code>{addr_s}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("sentiment_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            _addr_sent, _chain_sent = parts[1], parts[2]
            await query.edit_message_text(
                "\U0001f4c8 Use /sentiment to check market sentiment.\n\n"
                "Or use /ta for price-based technical analysis.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("investigate_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr_inv, _chain_inv = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f575\ufe0f Use /investigate <code>{addr_inv}</code> for deep investigation.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("ta_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            addr_ta, _chain_ta = parts[1], parts[2]
            await query.edit_message_text(
                f"\U0001f4ca Use /ta <code>{addr_ta}</code> for technical analysis.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("sentiment_refresh"):
        # Routed to the ported inline sentiment renderer.
        await _inline_sentiment(update, ctx)
    elif data.startswith("trending_chain_"):
        # Switch chain: trending_chain_{chain}
        new_chain = data[len("trending_chain_") :]
        if new_chain in ("solana", "ethereum", "base", "bsc", "arbitrum"):
            await query.edit_message_text(
                f"\U0001f525 <b>Loading {new_chain.capitalize()} trending...</b>",
                parse_mode=ParseMode.HTML,
            )
            from rugmunchbot.handlers.market import render_trending

            trend_text, trend_kb = await render_trending(new_chain, limit=8, window="24h")
            if trend_text is None or trend_kb is None:
                await query.edit_message_text(
                    "\u274c Could not fetch trending data.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return
            await query.edit_message_text(
                trend_text,
                parse_mode=ParseMode.HTML,
                reply_markup=trend_kb,
                disable_web_page_preview=True,
            )
        else:
            await query.answer("Chain not supported.", show_alert=True)
    elif data.startswith("trending_win_"):
        parts = data.split("_", 3)
        if len(parts) == 4 and parts[3] in ("1h", "24h", "7d"):
            win_chain, win = parts[2], parts[3]
            await query.edit_message_text(
                "\U0001f525 <b>Loading trending...</b>", parse_mode=ParseMode.HTML
            )
            from rugmunchbot.handlers.market import render_trending

            trend_text, trend_kb = await render_trending(win_chain, limit=8, window=win)
            if trend_text is None or trend_kb is None:
                await query.edit_message_text(
                    "\u274c Could not fetch trending data.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return
            await query.edit_message_text(
                trend_text,
                parse_mode=ParseMode.HTML,
                reply_markup=trend_kb,
                disable_web_page_preview=True,
            )
        else:
            await query.answer("Invalid request.", show_alert=True)
    elif data.startswith("market_refresh"):
        await _inline_market_data(update, ctx, "market")
    elif data.startswith("movers_refresh"):
        await _inline_market_data(update, ctx, "movers")
    elif data.startswith("launches_refresh"):
        await _inline_market_data(update, ctx, "launches")
    elif data.startswith("whales_refresh"):
        await _inline_market_data(update, ctx, "whales")
    elif data.startswith("deep_"):
        # TODO(port): Deep-Scan Stars invoice is private billing (scrub #17). [REVIEW]
        await _billing_unavailable(query, "\U0001f52c <b>Deep Token Scan</b>")
    elif data.startswith("sub_"):
        # TODO(port): tier subscription invoices are private billing (scrub #17). [REVIEW]
        await _billing_unavailable(query, "\u2b50 <b>Subscription</b>")
    elif data.startswith("topup_"):
        # TODO(port): Stars top-up invoices are private billing (scrub #17). [REVIEW]
        await _billing_unavailable(query, "\U0001f50b <b>Top Up</b>")


# ══════════════════════════════════════════════
# v5 — run_* command cards dispatch
# ══════════════════════════════════════════════


async def _dispatch_run_cmd(update: Update, ctx, cmd_arg: str | None = None) -> None:
    """Handle run_* menu buttons: live inline data for trending/news/sentiment,
    usage cards for everything else.

    ``cmd_arg`` is the raw callback payload after the ``run_`` prefix (from
    the callback handler). It is optional — when omitted (defensive) the
    command is re-derived from ``query.data``.
    """
    query = update.callback_query
    if query is None:
        return
    cmd = (query.data or "")[4:]

    if cmd == "trending":
        await _inline_trending(update, ctx)
        return
    if cmd == "news":
        await _inline_news(update, ctx)
        return
    if cmd == "sentiment":
        await _inline_sentiment(update, ctx)
        return
    if cmd in ("market", "movers", "launches", "whales"):
        await _inline_market_data(update, ctx, cmd)
        return

    card = _RUN_CARDS.get(cmd)
    if card is None:
        card = f"<code>/{cmd}</code> \u2014 check /help for usage."
    await query.edit_message_text(
        card + footer_links(),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )
