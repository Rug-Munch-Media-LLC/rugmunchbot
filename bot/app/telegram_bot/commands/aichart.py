"""Enhanced /aichart — RugCharts-powered chart with AI analysis overlay."""
import logging

from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.aichart")

async def aichart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "📊 *AI Chart* — RugCharts Enhanced\n\n"
            "Chart with volume authenticity, dev reputation, and AI analysis.\n\n"
            "Usage: `/aichart <token> [chain] [days]`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    token = context.args[0].upper()
    import re

    from app.chart_generator import chart_command as gen_chart
    chain = "ethereum" if token.startswith("0x") else "solana" if re.match(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$', token) else "solana"
    chain = context.args[1] if len(context.args) > 1 and context.args[1] in ["solana","ethereum","bsc","base"] else chain
    days = min(int(context.args[2]), 90) if len(context.args) > 2 else 7
    msg = await update.message.reply_text(f"📊 Generating AI chart for *{token}*...", parse_mode=ParseMode.MARKDOWN)
    try:
        png, caption = await gen_chart(f"{token}/USDT" if "/" not in token else token, chain, days)
        if png:
            await msg.delete()
            await update.message.reply_photo(photo=png, caption=caption, parse_mode=ParseMode.MARKDOWN)
        else:
            await msg.edit_text(caption, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")

handlers = [CommandHandler("aichart", aichart_command)]
