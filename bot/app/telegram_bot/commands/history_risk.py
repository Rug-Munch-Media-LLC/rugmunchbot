"""Time-travel risk — shows historical risk score for any scanned token."""
import logging

import asyncpg
from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.history_risk")

async def history_risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show how risk score changed over time for a token."""
    if not context.args:
        await update.message.reply_text(
            "⏳ *Time-Travel Risk*\n\nSee how a token's risk score has changed.\n\n"
            "Usage: `/riskhistory <address>`\n\n"
            "Example: `/riskhistory So11111111111111111111111111111111111111112`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    address = context.args[0].lower()
    chain = "ethereum" if address.startswith("0x") else "tron" if address.startswith("T") else "solana"

    await update.message.reply_text(f"⏳ Checking risk history for `{address[:12]}...`", parse_mode=ParseMode.MARKDOWN)

    try:
        conn = await asyncpg.connect("postgresql://rmi:RMI_PROD_POSTGRES_2026@127.0.0.1:5432/rmi", timeout=5)
        rows = await conn.fetch(
            "SELECT risk_score, risk_tier, scanned_at FROM scan_history "
            "WHERE address=$1 AND chain=$2 ORDER BY scanned_at DESC LIMIT 10",
            address, chain
        )
        await conn.close()

        if not rows:
            await update.message.reply_text("ℹ️ No scan history for this token. Use /scan first.")
            return

        text = f"⏳ *Risk History:* `{address[:12]}...`\n\n"
        for r in rows:
            icon = "🟢" if r["risk_score"] < 20 else "🟡" if r["risk_score"] < 40 else "🟠" if r["risk_score"] < 60 else "🔴"
            import datetime
            dt = datetime.datetime.fromtimestamp(r["scanned_at"])
            text += f"{icon} **{r['risk_score']}/100** ({dt.strftime('%b %d')})\n"

        # Show trend
        if len(rows) >= 2:
            first = rows[-1]["risk_score"]
            last = rows[0]["risk_score"]
            diff = last - first
            trend = "📈 increasing" if diff > 0 else "📉 decreasing" if diff < 0 else "➡️ stable"
            text += f"\n*Trend:* {trend} ({'+' if diff > 0 else ''}{diff} points)"

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)[:100]}")

handlers = [CommandHandler("riskhistory", history_risk_command)]
