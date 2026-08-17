#!/usr/bin/env python3
"""
RugMunchBot -- /deepscan command: ML + contract-analysis deep scan.

Wraps ``app.deep_scan.deep_scan_address`` — the platform's deep-scan
orchestrator that composes fraud-GNN / pyod / pygod anomaly scores, bytecode
decompilation (heimdall), sell simulation (pyrevm) and Slither static
analysis into one :class:`DeepScanReport`.

The report is rendered by the CANONICAL deep-scan formatter
``formatters._scan_report_deepscan.format_deep_ml_scan_report`` (same
skeleton as /scan and /scandeep: verdict → key metrics → evidence →
what-to-watch), so the ML deep scan stays on ONE formatter path.

Conventions follow ``handlers/osint.py`` (reply with a status message, then
``edit_text`` the result; ``back_kb`` on usage/errors). The scan is blocking
(network RPC + ML + EVM simulation) so it runs via ``asyncio.to_thread`` to
keep the event loop responsive. Graceful degradation: a missing module/dep or
any failure becomes a helpful message instead of a crash.
"""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.formatters import (
    format_deep_ml_scan_report,
    paywall_text,
    render_analyst_read_block,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    failure_card,
    usage_hint,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.states import check_spam, is_owner
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.deepscan")


async def cmd_deepscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/deepscan <address> [chain] — ML + contract-analysis deep scan.

    Runs fraud-GNN / pyod / pygod anomaly scores, bytecode decompilation,
    sell simulation and (when source is available) Slither analysis.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    # Per-user spam cooldown (3s) — mirror /scan: silent drop for rapid fire.
    if check_spam(user.id):
        return

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f52c <b>Deep ML Scan</b>\n\n"
            + usage_hint(
                "deepscan",
                "address [chain]",
                "0xBE0eB53FC46b790099138e3d32C721856d41e865",
                examples=("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana",),
                extra=(
                    "ML anomaly scores (fraud-GNN, pyod, pygod), decompiled ABI, "
                    "sell-simulation honeypot/tax/blacklist flags and top findings.\n\n"
                    "\u23f3 Can take 30-90s (RPC + ML + EVM simulation)."
                ),
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = ctx.args[0].strip()
    if len(address) < 20 or any(c.isspace() for c in address):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid address</b>\n\nPass a full EVM <code>0x\u2026</code> or Solana base58 address.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    chain = ctx.args[1].strip().lower() if len(ctx.args) > 1 else "auto"

    # Weekly scan quota per tier — same gate as /scan (owner bypass). Runs
    # AFTER arg validation so a bare usage call never consumes quota, and
    # BEFORE the expensive 30-90s ML/contract scan.
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f52c <b>Deep scan started...</b>\n<code>{html.escape(address)}</code>\n\n"
        "\u23f3 Running ML + contract analysis (30-90s)...",
        parse_mode=ParseMode.HTML,
    )
    try:
        from app.deep_scan import deep_scan_address
    except (ImportError, AttributeError) as exc:
        logger.warning("deep_scan module unavailable: %s", exc)
        await msg.edit_text(
            "\u26a0\ufe0f <b>Deep scan unavailable</b>\n\n"
            "The deep-scan engine is not installed on this server. "
            "Check that the ML/contract modules (heimdall-py, pyrevm, pyod) are provisioned.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    try:
        report = await asyncio.to_thread(deep_scan_address, address, chain=chain)
        # AI "Analyst Read" — plain-language bundle/cluster interpretation,
        # rendered BEFORE the deterministic sections. Fail-open: an empty
        # block (LLM down / no bundle data in the report) is skipped.
        analyst_read = await render_analyst_read_block(report.to_dict())
        await msg.edit_text(
            format_deep_ml_scan_report(report, analyst_read=analyst_read),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # deep scan is best-effort; never crash the handler
        logger.warning("deepscan failed for %s: %s", address, exc)
        await msg.edit_text(
            failure_card(
                "Deep Scan Failed",
                f"<code>{html.escape(address)}</code>\n\nCould not complete the deep scan.",
                ACTION_CHECK_ADDRESS,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
