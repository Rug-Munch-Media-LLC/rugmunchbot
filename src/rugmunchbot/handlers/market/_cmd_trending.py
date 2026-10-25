"""market /trending — hot tokens from the fused multi-source feed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode

from rugmunchbot.handlers._market_helpers import _resolve_chain

from ._render import render_trending

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /trending [chain] — hot tokens from fused multi-source data."""
    chain = _resolve_chain(ctx.args[0]) if ctx.args else "all"
    msg = await update.message.reply_text(
        "🔥 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    text, trending_kb = await render_trending(chain, limit=20, window="24h")
    if text is None or trending_kb is None:
        await msg.edit_text(
            "❌ <b>Trending Unavailable</b>\n\nCould not fetch trending tokens right now.\nPlease try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
        return
    await msg.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=trending_kb,
        disable_web_page_preview=True,
    )
