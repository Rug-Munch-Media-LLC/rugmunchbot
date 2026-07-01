"""Telegram /watch command — subscribe to alerts on any token/wallet."""
import logging

from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.watch")

async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "👁 *Watchlist*\n\n"
            "Subscribe to alerts on any token or wallet.\n"
            "Get notified on: risk changes, whale moves, price swings.\n\n"
            "Usage: `/watch <address>`\n"
            "       `/watch list` — view your watched addresses\n"
            "       `/watch remove <address>` — stop watching\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    cmd = context.args[0]
    user_id = update.effective_user.id

    if cmd == "list":
        # Show watched addresses (stored in DB)
        try:
            import asyncpg
            conn = await asyncpg.connect(
                "postgresql://rmi:RMI_PROD_POSTGRES_2026@127.0.0.1:5432/rmi", timeout=5)
            rows = await conn.fetch(
                "SELECT address, chain FROM watchlist WHERE user_id = $1 ORDER BY created_at DESC LIMIT 20",
                user_id)
            await conn.close()
            if rows:
                text = "👁 *Your Watchlist*\n" + "\n".join(
                    f"• `{r['address'][:12]}...` ({r['chain']})" for r in rows)
            else:
                text = "ℹ️ No watched addresses. Use `/watch <address>` to add."
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)[:50]}")
        return

    if cmd == "remove" and len(context.args) > 1:
        address = context.args[1]
        try:
            import asyncpg
            conn = await asyncpg.connect(
                "postgresql://rmi:RMI_PROD_POSTGRES_2026@127.0.0.1:5432/rmi", timeout=5)
            await conn.execute(
                "DELETE FROM watchlist WHERE user_id = $1 AND address = $2", user_id, address)
            await conn.close()
            await update.message.reply_text(f"✅ Removed `{address[:12]}...` from watchlist",
                                            parse_mode=ParseMode.MARKDOWN)
        except: pass
        return

    # Add to watchlist
    address = cmd
    chain = "ethereum" if address.startswith("0x") else "tron" if address.startswith("T") else "solana"

    try:
        import asyncpg
        conn = await asyncpg.connect(
            "postgresql://rmi:RMI_PROD_POSTGRES_2026@127.0.0.1:5432/rmi", timeout=5)
        await conn.execute("""
            INSERT INTO watchlist (user_id, address, chain, created_at) 
            VALUES ($1, $2, $3, EXTRACT(EPOCH FROM NOW()))
            ON CONFLICT (user_id, address) DO NOTHING""",
            user_id, address.lower(), chain)
        await conn.close()
        await update.message.reply_text(
            f"👁 Now watching `{address[:12]}...`\n"
            f"You'll be notified on risk changes and large transactions.",
            parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)[:50]}")

handlers = [CommandHandler("watch", watch_command)]
