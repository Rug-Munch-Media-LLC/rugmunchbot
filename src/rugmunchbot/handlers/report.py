#!/usr/bin/env python3
"""
RugMunchBot -- /report command: user-submitted scam reports (v6).

Usage:
  /report <address> [reason]   -> record a scam report, feed the shared
                                   known-scam denylist, notify the owner.

Reports are stored in the bot DB (db.scam_reports) and mirrored into the
in-memory anti-abuse denylist so future shares of the same address are
blocked bot-wide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import anti_abuse, db
from app.domains.telegram.rugmunchbot.config import OWNER_IDS
from app.domains.telegram.rugmunchbot.formatters import detect_chain
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.report")


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Record a user-submitted scam report."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(
            "\U0001f6ab <b>Report a Scam</b>\n\n"
            + usage_hint(
                "report",
                "address [reason]",
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933 honeypot — can't sell",
                extra=(
                    "Found a token that looks like a honeypot, rug pull or scam? "
                    "Report it and we'll investigate.\n\n"
                    "\U0001f4a1 Reports are reviewed by the Rug Munch team and feed "
                    "our shared scam denylist — you're helping everyone stay safe."
                ),
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    address = ctx.args[0].strip().lower()
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else ""

    # Basic address sanity: must look like an EVM or Solana address.
    from app.domains.telegram.rugmunchbot.formatters import is_evm, is_sol

    if not (is_evm(address) or is_sol(address)):
        await update.message.reply_text(
            failure_card(
                "Invalid Address",
                "That doesn't look like a valid token address. Please send an EVM (0x...) or Solana address.",
                "Example: /report <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code> honeypot",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    if len(address) > 64:
        await update.message.reply_text(
            failure_card(
                "Invalid Address",
                "Address is too long to be a contract address.",
                ACTION_CHECK_ADDRESS,
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    chain = detect_chain(address)
    ok = db.report_scam(user.id, address, chain, reason)
    if not ok:
        await update.message.reply_text(
            failure_card(
                "Report Failed",
                "Could not store the report.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    # Mirror into the in-memory denylist so this address is blocked immediately.
    anti_abuse.register_reported_scam(address)

    # Notify the owner (best effort — never crash the bot on a notify failure).
    for owner_id in OWNER_IDS:
        try:
            await ctx.bot.send_message(
                owner_id,
                f"\U0001f6a8 <b>New Scam Report</b>\n\n"
                f"\U0001f464 User: <code>{user.id}</code>\n"
                f"\U0001f50d Address: <code>{address}</code>\n"
                f"\U0001f30e Chain: {chain}\n"
                f"\U0001f4dd Reason: {reason[:200] or 'n/a'}\n\n"
                f"Use /admin_user <code>{user.id}</code> to inspect the reporter.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.debug("scam report owner notify failed for %s", owner_id, exc_info=True)

    await update.message.reply_text(
        f"\u2705 <b>Scam report submitted</b>\n\n"
        f"\U0001f50d Address: <code>{address}</code>\n"
        f"\U0001f30e Chain: {chain}\n\n"
        "Thank you! Our team will investigate. This address is now flagged "
        "in the shared denylist so other users get warned too.",
        parse_mode=ParseMode.HTML,
    )


__all__ = ["cmd_report"]
