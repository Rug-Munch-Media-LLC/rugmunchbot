"""Telegram /chart command — generates branded price charts using local chart_generator."""

# ⚠️ DEAD CODE — NOT WIRED INTO MAIN BOT
# This module is NOT imported or registered by bot.py.
# Kept as reference for future migration to rmi-backend.
# DO NOT add production credentials to this file.

import logging
import sys

from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.chart")
sys.path.insert(0, "/opt/rmi-tools/lib/python3.12/site-packages")


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /chart <token> [chain] [days] — generate branded price chart."""
    if not context.args:
        await update.message.reply_text(
            "📊 *Chart Generator*\n\n"
            "Usage: `/chart <token> [chain] [days]`\n\n"
            "Examples:\n"
            "  `/chart SOL` — SOL/USDT, solana, 7d\n"
            "  `/chart BTC ethereum 30` — BTC/USDT, 30d\n"
            "  `/chart BONK solana 1` — BONK, 1d\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    token = context.args[0].upper()
    chain = context.args[1].lower() if len(context.args) > 1 else "solana"
    days = min(int(context.args[2]), 90) if len(context.args) > 2 else 7

    msg = await update.message.reply_text(
        f"📊 Generating chart for *{token}*... ({chain}, {days}d)",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        from app.chart_generator import chart_command as gen_chart

        png, caption = await gen_chart(f"{token}/USDT", chain, days)

        if png:
            await msg.delete()
            await update.message.reply_photo(
                photo=png,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await msg.edit_text(caption, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Chart failed: {e}")
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")


handlers = [CommandHandler("chart", chart_command)]
