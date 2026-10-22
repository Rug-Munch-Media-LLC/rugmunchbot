#!/usr/bin/env python3
"""Callback branches for the verdict-first scan card.

Two button payloads, both carrying ONLY the address (Telegram caps
``callback_data`` at 64 bytes and a Solana address is already 44):

  * ``vcard_refresh_<address>`` — re-scan (cache bypassed) and EDIT the same
    message in place, so a refreshed card never spawns a second message;
  * ``vcard_full_<address>``    — swap the card for the full legacy report in
    the same message (cached payload when fresh, otherwise one rescan);
  * ``vcard_fb_<address>``      — swap the card for the top-10 first-buyer
    list; a scan with no first-buyer payload says so instead of inventing
    rows.

Both answer the callback immediately and fail closed onto a small failure
card; an unchanged card (Telegram ``Message is not modified``) is reported
as "no change" instead of escaping as an error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import telegram
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.formatters import detect_chain, format_scan_report, is_owner
from rugmunchbot.formatters._scan_report_parity import fit_report
from rugmunchbot.formatters._vcard import format_verdict_card
from rugmunchbot.formatters._vcard_buyers_view import full_buyers_view
from rugmunchbot.handlers._messages import ACTION_CHECK_ADDRESS, failure_card
from rugmunchbot.handlers._scan_handlers._vcard_refresh import load_scan
from rugmunchbot.keyboards import back_kb, vcard_kb
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.vcard")

PREFIXES = ("vcard_refresh_", "vcard_full_", "vcard_fb_")


def _short(address: str) -> str:
    return f"{address[:4]}\u2026{address[-4:]}" if len(address) > 10 else address


async def _edit(query: Any, text: str, kb: Any) -> None:
    """Edit the card in place; an identical card is not an error."""
    try:
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except telegram.error.BadRequest as exc:
        if "not modified" in str(exc).lower():
            await query.answer("No change since the last scan.", show_alert=False)
            return
        raise


async def _build(query: Any, address: str, *, fresh: bool) -> Any:
    """Scan + render the card; returns the reply markup to attach."""
    chain = detect_chain(address)
    await query.edit_message_text(
        f"\U0001f504 Refreshing <code>{_short(address)}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    result = await load_scan(address, chain, fresh=fresh)
    if result is None:
        await _edit(
            query,
            failure_card(
                "Refresh Failed", "The scanner did not answer this time.", ACTION_CHECK_ADDRESS
            ),
            back_kb(),
        )
        return None
    await _edit(query, format_verdict_card(result), vcard_kb(address, chain))
    return result


async def handle(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    query: Any,
    data: str,
) -> bool:
    """Handle verdict-card callbacks. True when a branch matched."""
    if not data.startswith(PREFIXES):
        return False
    user = getattr(query, "from_user", None)
    user_id = getattr(user, "id", 0) or 0
    address = data.split("_", 2)[-1].strip()
    if not address:
        await query.answer("Missing address.", show_alert=True)
        return True
    if not is_owner(user_id):
        allowed, _used, _limit = db.check_rate_limit(user_id, "scan")
        if not allowed:
            await query.answer("Rate limit reached \u2014 try again in a minute.", show_alert=True)
            return True

    if data.startswith("vcard_refresh_"):
        await query.answer("Refreshing\u2026")
        await _build(query, address, fresh=True)
        return True

    if data.startswith("vcard_fb_"):
        await query.answer("Opening first buyers\u2026")
        chain = detect_chain(address)
        result = await load_scan(address, chain)
        text = full_buyers_view(result) if result is not None else ""
        if not text:
            await _edit(
                query,
                failure_card(
                    "No First Buyers",
                    "This scan carries no first-buyer data (indexer miss or pre-forensic payload).",
                    ACTION_CHECK_ADDRESS,
                ),
                vcard_kb(address, chain),
            )
            return True
        await _edit(query, text, vcard_kb(address, chain))
        return True

    await query.answer("Opening full report\u2026")
    chain = detect_chain(address)
    result = await load_scan(address, chain)
    if result is None:
        await _edit(
            query,
            failure_card("Report Unavailable", "The scanner did not answer.", ACTION_CHECK_ADDRESS),
            back_kb(),
        )
        return True
    await _edit(query, fit_report(format_scan_report(result)), vcard_kb(address, chain))
    return True
