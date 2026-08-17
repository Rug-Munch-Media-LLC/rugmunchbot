#!/usr/bin/env python3
"""
RugMunchBot -- Payment handlers.

Telegram Stars payment pre-checkout and successful payment routing.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BUY_STARS_PACKS,
    TIERS,
    TOP_UP_PACKS,
)
from app.domains.telegram.rugmunchbot.keyboards import scan_result_kb
from app.domains.telegram.rugmunchbot.scanner import scan_token
from app.domains.telegram.rugmunchbot.states import _pending_deep_scans, _pending_monitors
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

_SEP = "\u2501" * 28


async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Validate a Telegram Stars pre-checkout query against known payload prefixes."""
    query = update.pre_checkout_query
    payload = query.invoice_payload  # type: ignore[union-attr]  # PTB None-able fields
    valid_prefixes = ("buystars_", "topup_", "sub_", "deep_", "mon_")
    if any(payload.startswith(p) for p in valid_prefixes):
        await query.answer(ok=True)  # type: ignore[union-attr]  # PTB None-able fields
    else:
        logger.warning("Unknown invoice payload: %s", payload)
        await query.answer(ok=False, error_message="Invalid invoice.")  # type: ignore[union-attr]  # PTB None-able fields


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Credit a confirmed Stars payment: buystars/topup/sub/deep/mon branches."""
    payment = update.message.successful_payment  # type: ignore[union-attr]  # PTB None-able fields
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    payload = payment.invoice_payload  # type: ignore[union-attr]  # PTB None-able fields
    charge_id = payment.telegram_payment_charge_id  # type: ignore[union-attr]  # PTB None-able fields
    total = payment.total_amount  # type: ignore[union-attr]  # PTB None-able fields

    if payload.startswith("buystars_"):
        pack_key = payload[9:]
        pack = BUY_STARS_PACKS.get(pack_key)
        stars_to_add = pack["stars"] if pack else total
        db.add_stars(user.id, stars_to_add, "purchase", charge_id)
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 <b>Stars Purchased!</b>\n\n"
            f"\u2b50 +{stars_to_add:,} Stars added to your balance.\n"
            f"Use /topup to spend them on scans and subscriptions!",
            parse_mode="HTML",
        )

    elif payload.startswith("topup_"):
        pack_key = payload[6:]
        pack = TOP_UP_PACKS.get(pack_key)
        if pack:
            conn = db.get_db()
            if pack["type"] == "scans":
                conn.execute(
                    "UPDATE users SET bonus_scans = bonus_scans + ? WHERE user_id = ?",
                    (pack["amount"], user.id),
                )
            elif pack["type"] == "ai_msgs":
                conn.execute(
                    "UPDATE users SET bonus_ai_msgs = bonus_ai_msgs + ? WHERE user_id = ?",
                    (pack["amount"], user.id),
                )
            conn.execute(
                "INSERT INTO star_transactions (user_id, amount, action, telegram_payment_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user.id, -pack["stars"], f"topup_{pack_key}", charge_id, int(time.time())),
            )
            conn.commit()
            conn.close()
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                f"\u2705 <b>Purchase Successful!</b>\n\n"
                f"+{pack['amount']} bonus {pack['type'].replace('_', ' ')}\n"
                f"Cost: \u2b50{pack['stars']}\n\n"
                f"These never expire!",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                "\u2705 Payment received but pack not found. Contact support.",
                parse_mode="HTML",
            )

    elif payload.startswith("sub_"):
        tier_key = payload[4:]
        tier = TIERS.get(tier_key)
        if tier and tier_key != "free":
            db.activate_tier(user.id, tier_key, 30)
            tc = TIERS[tier_key]
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                f"\U0001f389 <b>Welcome to {tc['emoji']} {tc['name']}!</b>\n\n"
                f"Your {tc['name']} tier is now active for 30 days.\n"
                f"\u00b7 {tc['scans_per_week']} scans/week\n"
                f"\u00b7 {tc['ai_msgs_per_week']} AI messages/week\n"
                f"\u00b7 All premium features unlocked!\n\n"
                f"Use /account to see your dashboard.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                "\u2705 Payment received but tier not found. Contact support.",
                parse_mode="HTML",
            )

    elif payload.startswith("deep_"):
        token = payload[5:]
        pending = _pending_deep_scans.pop(token, None)
        if pending and pending["user_id"] == user.id:
            msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                f"\U0001f52c <b>Deep Scan Started</b>\n\n"
                f"Running enhanced analysis on <code>{pending['address']}</code>...",
                parse_mode="HTML",
            )
            try:
                result = await scan_token(pending["address"], pending["chain"])
                report_lines = [
                    "\U0001f52c <b>Deep Token Scan</b>",
                    _SEP,
                    "",
                ]
                from app.domains.telegram.rugmunchbot.formatters import format_scan_report

                report = format_scan_report(result)
                full_report = "\n".join(report_lines) + report
                db.log_scan(user.id, pending["address"], result["chain"], "deep", result["risk_score"])
                await msg.edit_text(
                    full_report,
                    parse_mode="HTML",
                    reply_markup=scan_result_kb(pending["address"], result["chain"]),
                    disable_web_page_preview=True,
                )
            except (TelegramError, ValueError, KeyError, TypeError) as e:
                logger.error(f"Deep scan payment error: {e}")
                await msg.edit_text(
                    "\u274c Deep scan failed.\n\nPlease try again in a moment. Contact support if it persists.",
                    parse_mode="HTML",
                )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                "\u2705 Payment received but the scan request expired. Please run /scan again.",
                parse_mode="HTML",
            )

    elif payload.startswith("mon_"):
        token = payload[4:]
        pending = _pending_monitors.pop(token, None)
        if pending and pending["user_id"] == user.id:
            addr = pending["address"]
            chain = pending["chain"]
            msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                f"\U0001f6f0\ufe0f <b>Monitor Started</b>\n\nRegistering live monitoring for <code>{addr}</code>...",
                parse_mode="HTML",
            )
            baseline: dict[str, Any] = {}
            deployer: str | None = None
            symbol: str | None = None
            try:
                from app.domains.telegram.rugmunchbot.handlers.alerts import _monitor_baseline

                result = await scan_token(addr, chain)
                baseline = _monitor_baseline(result)
                deployer = result.get("deployer_address")
                symbol = result.get("symbol")
            except (TelegramError, ValueError, KeyError, TypeError) as e:
                logger.warning(f"Monitor baseline scan failed, continuing with empty baseline: {e}")

            ok = db.add_monitor(
                user.id,
                addr,
                chain,
                symbol=symbol,
                deployer_address=deployer,
                baseline=baseline,
            )
            if ok:
                from app.domains.telegram.rugmunchbot.handlers.alerts import _subscribe_backend_monitor

                await _subscribe_backend_monitor(user.id, addr)
                await msg.edit_text(
                    f"\U0001f6f0\ufe0f <b>Monitor Active</b>\n\n"
                    f"Token: <code>{addr}</code>\n"
                    f"Chain: {chain}\n"
                    f"\U0001f4cb Watching: LP changes, whale/deployer moves, "
                    f"tax/ownership/mint changes, price swings\n\n"
                    f"\U0001f514 You'll be DM'd when something meaningful changes.\n"
                    f"Manage: /monitors \u00b7 Stop: /unmonitor <code>{addr}</code>",
                    parse_mode="HTML",
                )
            else:
                await msg.edit_text(
                    f"\u2705 Payment received, but <code>{addr}</code> is already monitored. Use /monitors to manage.",
                    parse_mode="HTML",
                )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                "\u2705 Payment received but the monitor request expired. Please run /monitor again.",
                parse_mode="HTML",
            )

    else:
        db.add_stars(user.id, total, "payment", charge_id)
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 <b>Payment Received!</b>\n\n"
            f"\u2b50 {total} Stars added to your balance.\n"
            f"Use /topup to spend them.",
            parse_mode="HTML",
        )
