"""Telegram /history command — view past scan results."""
import logging

from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.history")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 10 scan results."""
    user_id = update.effective_user.id
    try:
        import asyncpg
        conn = await asyncpg.connect("postgresql://rmi:RMI_PROD_POSTGRES_2026@127.0.0.1:5432/rmi", timeout=5)
        rows = await conn.fetch(
            "SELECT address, chain, risk_score, risk_tier, scanned_at FROM scan_history "
            "WHERE user_id = $1 ORDER BY scanned_at DESC LIMIT 10", user_id)
        await conn.close()

        if not rows:
            await update.message.reply_text("ℹ️ No scan history yet. Use /scan to scan tokens.")
            return

        text = "📋 *Your Recent Scans*\n\n"
        for r in rows:
            icon = "🟢" if r["risk_score"] < 20 else "🟡" if r["risk_score"] < 40 else "🟠" if r["risk_score"] < 60 else "🔴" if r["risk_score"] < 80 else "⛔"
            addr = r["address"][:12] + "..."
            text += f"{icon} `{addr}` ({r['chain']}) — {r['risk_score']}/100 [{r['risk_tier']}]\n"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)[:100]}")

handlers = [CommandHandler("history", history_command)]
