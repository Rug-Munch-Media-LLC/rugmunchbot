"""Telegram /portfolio command — scan all tokens in a wallet at once."""
import logging

import httpx
from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.portfolio")

async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /portfolio <address> [chain] — scan all tokens in wallet."""
    if not context.args:
        await update.message.reply_text(
            "📊 *Portfolio Scanner*\n\n"
            "Scans ALL tokens in a wallet and shows risk overview.\n\n"
            "Usage: `/portfolio <address>`\n\n"
            "Examples:\n"
            "  `/portfolio 0xdAC17F958D2ee523a2206206994597C13D831ec7`\n"
            "  `/portfolio So11111111111111111111111111111111111111112`\n"
            "  `/portfolio T...` — auto-detects chain\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    address = context.args[0]
    chain = "ethereum" if address.startswith("0x") else "tron" if address.startswith("T") else "solana"

    msg = await update.message.reply_text(
        f"📊 Scanning portfolio: `{address[:12]}...` ({chain})",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            # Get wallet tokens from DataBus
            r = await c.post("http://127.0.0.1:8000/api/v1/databus/fetch", json={
                "data_type": "wallet_tokens", "address": address, "chain": chain
            })

            if r.status_code != 200:
                await msg.edit_text(f"❌ Portfolio fetch failed: {r.status_code}")
                return

            data = r.json().get("data", [])
            tokens = data if isinstance(data, list) else data.get("tokens", [])

            if not tokens:
                await msg.edit_text("ℹ️ No tokens found for this wallet.")
                return

            # Scan first 10 tokens for risk
            risk_scores = []
            for token in tokens[:10]:
                try:
                    scan_r = await c.post("http://127.0.0.1:8000/scanner/scan", json={
                        "chain": chain, "address": token.get("mint", token.get("address", "")),
                        "depth": "quick"
                    }, timeout=15)
                    if scan_r.status_code == 200:
                        risk_scores.append(scan_r.json().get("risk_score", 50))
                except: pass

            avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0
            high_risk = sum(1 for r in risk_scores if r >= 60)

            icon = "🟢" if avg_risk < 20 else "🟡" if avg_risk < 40 else "🟠" if avg_risk < 60 else "🔴"

            text = (
                f"{icon} *Portfolio Risk Overview*\n"
                f"Address: `{address[:12]}...`\n"
                f"Chain: {chain}\n"
                f"Tokens found: {len(tokens)}\n"
                f"Scanned: {len(risk_scores)} tokens\n\n"
                f"*Average Risk:* {avg_risk:.0f}/100\n"
                f"*High Risk:* {high_risk} tokens\n"
                f"*Safe:* {len(risk_scores) - high_risk} tokens\n"
            )

            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")

handlers = [CommandHandler("portfolio", portfolio_command)]
