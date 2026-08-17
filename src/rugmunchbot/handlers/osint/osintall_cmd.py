"""/osintall <target> [type] — run every applicable OSINT module at once."""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.osint._common import (
    _AGGREGATE_TIMEOUT,
    _OSINT_TYPES,
    logger,
)
from app.domains.telegram.rugmunchbot.handlers.osint._format import _format_aggregate_result
from app.domains.telegram.rugmunchbot.keyboards import back_kb

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_osintall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/osintall <target> [type] — run every applicable OSINT module at once.

    Auto-detects the target type (or takes an explicit type from the second
    arg), then runs all applicable modules in parallel via the engine's
    ``run_aggregate`` and merges every finding into one summary.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f575\ufe0f <b>OSINT Aggregate</b>\n\n"
            "Usage: /osintall <code>target</code> [type]\n\n"
            "Examples:\n"
            "  /osintall <code>scam-example.com</code>\n"
            "  /osintall <code>0xBE0eB53FC46b790099138e3d32C721856d41e865</code> wallet\n\n"
            "Auto-detects the target type (username / email / domain / wallet / "
            "phone / face) and runs every applicable module in parallel, then "
            "merges all findings into one summary.\n\n"
            "\u23f3 Can take up to 90s.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    target = ctx.args[0].strip()
    if not target:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid target</b>\n\n"
            "Pass a username, email, domain, wallet address, phone or photo description.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    target_type = ctx.args[1].strip().lower() if len(ctx.args) > 1 else "auto"
    if target_type != "auto" and target_type not in _OSINT_TYPES:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u26a0\ufe0f <b>Invalid type</b>\n\nExpected one of: <code>{', '.join(_OSINT_TYPES)}</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Investigating everything...</b>\n<code>{html.escape(target)}</code>\n\n"
        "\u23f3 Running all applicable OSINT modules (up to 90s)...",
        parse_mode=ParseMode.HTML,
    )
    try:
        from app.osint.engine import run_aggregate
    except (ImportError, AttributeError) as exc:
        logger.warning("osint engine unavailable: %s", exc)
        await msg.edit_text(
            "\u26a0\ufe0f <b>OSINT engine unavailable</b>\n\n"
            "The aggregate OSINT engine is not installed on this server.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    try:
        result = await asyncio.to_thread(run_aggregate, target, target_type, timeout=_AGGREGATE_TIMEOUT)
        await msg.edit_text(
            _format_aggregate_result(result),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # best-effort aggregate; never crash the handler
        logger.warning("osintall failed for %s: %s", target, exc)
        await msg.edit_text(
            "\u274c <b>OSINT Aggregate Failed</b>\n\n"
            f"<code>{html.escape(target)}</code>\n\n"
            "Could not complete the aggregate lookup. Please try again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
