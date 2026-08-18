#!/usr/bin/env python3
"""
RugMunchBot -- /deepscan command: backend API deep scan.

Calls the RMI backend's /api/v2/scanner/token/deepscan endpoint (by way of
``rugmunchbot.tools.scanner.sentinel_scan``) which runs 20+ scanner modules
across CORE, DEEP, PREMIUM, INTEL, and SENTINEL tiers, returning a unified
risk score and findings report. The same formatter path is used so /deepscan
and /sentinelscan render identically.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.formatters import format_sentinel_deepscan_section, paywall_text
from rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    failure_card,
    usage_hint,
)
from rugmunchbot.keyboards import back_kb, deep_scan_result_kb
from rugmunchbot.states import check_spam, is_owner
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.deepscan")


async def cmd_deepscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/deepscan <address> [chain] — full forensic token scan (20+ modules)."""
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if check_spam(user.id):
        return

    if not ctx.args:
        await update.message.reply_text(
            "\U0001f52c <b>Deep Token Scan</b>\n\n"
            + usage_hint(
                "deepscan",
                "address [chain]",
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                examples=(
                    "So11111111111111111111111111111111111111112 solana",
                    "0xBE0eB53FC46b790099138e3d32C721856d41e865 eth",
                ),
                extra=(
                    "Runs 20+ scanner modules: CORE + DEEP + PREMIUM + INTEL + SENTINEL. "
                    "Covers honeypot detection, liquidity analysis, holder distribution, "
                    "wallet metrics, bundle analysis, and more.\n\n"
                    "\u23f3 Takes 20-45s for a full forensic audit."
                ),
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = ctx.args[0].strip()
    if len(address) < 20 or any(c.isspace() for c in address):
        await update.message.reply_text(
            "\u26a0\ufe0f <b>Invalid address</b>\n\nPass a full EVM <code>0x\u2026</code> or Solana base58 address.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    chain = ctx.args[1].strip().lower() if len(ctx.args) > 1 else "solana"

    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

    msg = await update.message.reply_text(
        f"\U0001f52c <b>Deep scan started...</b>\n<code>{html.escape(address)}</code>\n\n"
        "\u23f3 Running 20+ forensic modules (20-45s)...",
        parse_mode=ParseMode.HTML,
    )
    try:
        from rugmunchbot.tools.scanner._scans import sentinel_scan

        report = await sentinel_scan(address, chain=chain)
        if report is None:
            raise RuntimeError("sentinel_scan returned None")

        formatted = format_sentinel_deepscan_section(report)
        await msg.edit_text(
            formatted,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=deep_scan_result_kb(address, chain),
        )
    except Exception as exc:
        logger.warning("deepscan failed for %s: %s", address, exc)
        await msg.edit_text(
            failure_card(
                "Deep Scan Failed",
                f"<code>{html.escape(address)}</code>\n\nCould not complete the deep scan. "
                "The backend may be busy or the token is unsupported.",
                ACTION_CHECK_ADDRESS,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
