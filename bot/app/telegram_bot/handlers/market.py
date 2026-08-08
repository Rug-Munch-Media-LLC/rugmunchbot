#!/usr/bin/env python3
"""
V3 market handler — /market fetches the market rundown from the llama-server
chat endpoint (Qwen3-4B) and renders it for the user.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handlers.menu import back_kb
from services.formatting import format_market_rundown
from services.llm import fetch_market_rundown
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    msg = await update.message.reply_text(
        "📊 <b>Fetching market rundown...</b>", parse_mode=ParseMode.HTML
    )
    await _edit_rundown(msg)


async def display_market(query) -> None:
    """Callback entry (menu_market) — edits the tapped message in place."""
    await query.edit_message_text("📊 <b>Fetching market rundown...</b>", parse_mode=ParseMode.HTML)
    await _edit_rundown(query.message)


async def _edit_rundown(message) -> None:
    try:
        text = await fetch_market_rundown()
        if not text.strip():
            raise ValueError("empty rundown")
        await message.edit_text(
            format_market_rundown(text),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.debug("market rundown failed: %s", exc)
        await message.edit_text(
            "❌ <b>Market Rundown Unavailable</b>\n\nPlease try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
