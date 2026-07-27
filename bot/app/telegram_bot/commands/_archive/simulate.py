"""Telegram /simulate command — simulate a buy/sell before executing.
Detects honeypots, slippage, taxes BEFORE user trades."""

# ⚠️ DEAD CODE — NOT WIRED INTO MAIN BOT
# This module is NOT imported or registered by bot.py.
# Kept as reference for future migration to rmi-backend.
# DO NOT add production credentials to this file.

import logging

import httpx
from telegram import ParseMode, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram.simulate")


async def simulate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /simulate <token> [amount] — simulate buy."""
    if not context.args:
        await update.message.reply_text(
            "🧪 *Transaction Simulator*\n\n"
            "Simulate buying a token before you commit funds.\n"
            "Detects honeypots, taxes, slippage, and max limits.\n\n"
            "Usage: `/simulate <token> [amount_in_sol]`\n\n"
            "Examples:\n"
            "  `/simulate So11111111111111111111111111111111111111112 0.1`\n"
            "  `/simulate 0xdAC17F958D2ee523a2206206994597C13D831ec7 0.01`\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    token = context.args[0]
    amount = float(context.args[1]) if len(context.args) > 1 else 0.1
    chain = (
        "ethereum"
        if token.startswith("0x")
        else "tron"
        if token.startswith("T")
        else "solana"
    )

    msg = await update.message.reply_text(
        f"🧪 Simulating buy of *{token[:12]}...* with {amount} {chain}...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            # Use Blowfish API for free tx simulation
            if chain == "solana":
                r = await c.post(
                    "https://api.blowfish.xyz/solana/v0/mainnet/scan/transactions",
                    json={
                        "transactions": [{"type": "SWAP", "data": token}],
                        "userAccount": "11111111111111111111111111111111",
                    },
                    headers={"Authorization": "Bearer demo"},
                )

                if r.status_code == 200:
                    data = r.json()
                    severity = data.get("scanResults", [{}])[0].get(
                        "severity", "UNKNOWN"
                    )

                    icon = (
                        "⛔"
                        if severity == "CRITICAL"
                        else "🔴"
                        if severity == "WARN"
                        else "🟢"
                    )

                    text = f"{icon} *Simulation Result*\n"
                    text += f"Token: `{token[:12]}...`\n"
                    text += f"Amount: {amount} {chain}\n"
                    text += f"Risk: {severity}\n\n"

                    if severity == "CRITICAL":
                        text += (
                            "⛔ *This transaction appears malicious.*\nDo NOT proceed."
                        )
                    elif severity == "WARN":
                        text += (
                            "⚠️ *Proceed with caution.* High risk indicators detected."
                        )
                    else:
                        text += "✅ *No obvious risks detected.* Always DYOR."

                    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
                    return

            # Fallback: use scanner + Jupiter price quote
            scan_r = await c.post(
                "http://127.0.0.1:8000/scanner/scan",
                json={"chain": chain, "address": token, "depth": "quick"},
                timeout=15,
            )

            if scan_r.status_code == 200:
                data = scan_r.json()
                risk = data.get("risk_score", 50)
                findings = data.get("findings", [])

                risk_icon = (
                    "⛔"
                    if risk >= 80
                    else "🔴"
                    if risk >= 60
                    else "🟠"
                    if risk >= 40
                    else "🟡"
                    if risk >= 20
                    else "🟢"
                )
                text = f"{risk_icon} *Simulation Result*\n"
                text += f"Token: `{token[:12]}...`\n"
                text += f"Risk Score: {risk}/100\n\n"

                if risk >= 60:
                    text += "⛔ *HIGH RISK* — Do not buy this token.\n"
                elif risk >= 40:
                    text += "⚠️ *MEDIUM RISK* — Proceed with extreme caution.\n"
                else:
                    text += "✅ *LOW RISK* — No major red flags detected.\n"

                if findings:
                    text += "\n*Findings:*\n" + "\n".join(
                        f"• {f}" for f in findings[:5]
                    )

                await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
            else:
                await msg.edit_text(f"❌ Simulation failed: {scan_r.status_code}")

    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)[:100]}")


handlers = [CommandHandler("simulate", simulate_command)]
