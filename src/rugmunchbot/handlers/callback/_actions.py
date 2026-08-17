#!/usr/bin/env python3
"""
RugMunchBot -- callback action branches.

Address/chain command dispatchers (``scan_``, ``holders_``, ``trace_``, ...),
scam-school, watchlist toggle, API revoke and upgrade cards. Split out of the
historic single-function ``handle_callback`` chain (2026-08-16).

Order is behavior-critical: ``dismiss_alert_`` precedes ``alert_`` and
``scam_`` precedes ``scan_`` exactly as in the original chain. A matched
prefix always returns True (the original if/elif chain stopped once a
``startswith`` branch was entered — even when its payload was malformed).
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import SCAM_SCHOOL_TOPICS, TIERS
from app.domains.telegram.rugmunchbot.keyboards import back_kb, scamschool_kb

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


def _addr_chain(data: str) -> tuple[str, str] | None:
    """Split a ``prefix_addr_chain`` callback into (addr, chain)."""
    parts = data.split("_", 2)
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


async def _run_addr_cmd(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    data: str,
    module: str,
    func: str,
    *,
    args: list[str] | None = None,
) -> bool:
    """Dispatch an ``prefix_addr_chain`` callback to a lazy-imported command.

    Mirrors the original branch bodies: when the payload carries fewer than
    three parts the branch is entered but does nothing — returns True so the
    dispatcher chain stops, exactly like the original if/elif.
    """
    pair = _addr_chain(data)
    if pair is None:
        return True
    addr, chain = pair
    handler = getattr(importlib.import_module(module), func)
    ctx.args = args if args is not None else [addr, chain]
    await handler(update, ctx)
    return True


async def handle(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    query: Any,
    user: Any,
    data: str,
) -> bool:
    """Handle scan/watch/scam/admin-action callbacks. True when matched."""
    if data.startswith("dismiss_alert_"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(
                "\u2705 Alert dismissed.",
                parse_mode=ParseMode.HTML,
            )
        except (ConnectionError, OSError, TimeoutError):
            pass
        await query.answer("Alert dismissed.")
    elif data.startswith("scam_"):
        topic_key = data[5:]
        topic = SCAM_SCHOOL_TOPICS.get(topic_key)
        if topic:
            await query.edit_message_text(topic["content"], parse_mode=ParseMode.MARKDOWN, reply_markup=scamschool_kb())
    elif data.startswith("scan_"):
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.scanning", "cmd_scan")
    elif data.startswith("share_"):
        pair = _addr_chain(data)
        if pair is not None:
            addr, chain = pair
            share_text = (
                f"\U0001f50d <b>RMI Token Scan</b>\n\n"
                f"<code>{addr}</code> ({chain.title()})\n\n"
                f"Scan this token with Rug Munch Bot:\n"
                f"https://t.me/RugMunchBot?start=scan_{addr}_{chain}"
            )
            await query.edit_message_text(
                share_text,
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("holders_"):
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.holders", "cmd_holders")
    elif data.startswith("trace_"):
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.trace", "cmd_trace")
    elif data.startswith("wallet_"):
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.scanning", "cmd_wallet")
    elif data.startswith("chart_"):
        pair = _addr_chain(data)
        if pair is not None:
            addr, chain = pair
            await query.edit_message_text(
                f"\U0001f4c8 <b>Generating chart...</b>\n<code>{addr}</code>",
                parse_mode=ParseMode.HTML,
            )
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.commands.chart", "cmd_chart")
    elif data.startswith("rugmap_"):
        pair = _addr_chain(data)
        if pair is not None:
            addr, chain = pair
            await query.edit_message_text(
                f"\U0001f5fa\ufe0f <b>Generating RugMap...</b>\n<code>{addr}</code>",
                parse_mode=ParseMode.HTML,
            )
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.commands.rugmap", "cmd_rugmap")
    elif data.startswith("alert_"):
        await _run_addr_cmd(
            update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.alerts", "cmd_alerts", args=[]
        )
    elif data.startswith("upgrade_"):
        tier_key = data[8:]
        if tier_key in TIERS:
            tier_info = TIERS[tier_key]
            await query.edit_message_text(
                f"{tier_info['emoji']} <b>{tier_info['name']} — {tier_info['price_stars']:,}\u2605/30d</b>\n\n"
                f"Unlock {tier_info['scans_per_week']} scans/week, "
                f"{tier_info['ai_msgs_per_week']} AI messages/week, and:\n"
                + "\n".join(f"• {f}" for f in tier_info["features"][:6])
                + "\n\nSubscribe now and get instant access!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton(f"⭐ Subscribe {tier_info['name']}", callback_data=f"sub_{tier_key}")],
                        [InlineKeyboardButton("🔙 Plans", callback_data="menu_pricing")],
                    ]
                ),
            )
    elif data == "api_revoke":
        await query.edit_message_text(
            "🔄 Revoke all active API tokens?\n\nThis will immediately invalidate all tokens.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Yes, Revoke All", callback_data="api_revoke_confirm")],
                    [InlineKeyboardButton("🔙 Cancel", callback_data="menu_main")],
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
            "✅ All API tokens revoked.\n\nGenerate new ones with /api create.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data.startswith("watch_"):
        pair = _addr_chain(data)
        if pair is not None:
            addr, chain = pair
            ok = db.add_watchlist(user.id, addr, chain)
            await query.answer("Added to watchlist!" if ok else "Could not add (limit reached?)", show_alert=True)
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
            from app.domains.telegram.rugmunchbot.handlers.research import cmd_research

            ctx.args = [addr_s]
            await cmd_research(update, ctx)
    elif data.startswith("sentiment_"):
        await _run_addr_cmd(
            update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.sentiment", "cmd_sentiment", args=[]
        )
    elif data.startswith("investigate_"):
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.osint", "cmd_investigate")
    elif data.startswith("ta_"):
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.handlers.scanning", "cmd_ta")
    elif data.startswith("mev_"):
        await _run_addr_cmd(update, ctx, data, "app.domains.telegram.rugmunchbot.commands.premium", "cmd_mev")
    else:
        return False
    return True
