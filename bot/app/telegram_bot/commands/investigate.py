"""Investigation commands for @rugmunchbot — /investigate, /case, /watchlist."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


async def investigate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/investigate <address> [chain] — run a full investigation."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /investigate <address> [chain]")
        return
    address = args[0]
    chain = args[1] if len(args) > 1 else "auto"
    await update.message.reply_text(f"🔍 Running investigation on {address[:12]}...")

    # Call investigation API
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            # Run all checks in parallel
            tasks = {
                "sanctions": f"http://127.0.0.1:8002/api/v1/investigation/sanctions?address={address}",
                "deployer": f"http://127.0.0.1:8002/api/v1/investigation/deployer?address={address}",
                "decompile": f"http://127.0.0.1:8002/api/v1/investigation/decompile?address={address}",
            }
            results = {}
            for name, url in tasks.items():
                try:
                    r = await c.get(url, timeout=10)
                    if r.status_code == 200:
                        results[name] = r.json()
                except:
                    pass

            # Build response
            lines = [f"*Investigation Report: `{address[:12]}...`*", ""]
            if "sanctions" in results:
                s = results["sanctions"]
                lines.append(f"*Sanctions:* {'🚫 FLAGGED' if s.get('flags') else '✅ Clean'}")
            if "deployer" in results:
                d = results["deployer"]
                lines.append(
                    f"*Deployer Rep:* {d.get('reputation','?')} ({d.get('total_tokens',0)} tokens)"
                )
            if "decompile" in results:
                dc = results["decompile"]
                if dc.get("is_contract"):
                    lines.append(
                        f"*Contract:* {dc.get('code_size','?')} bytes, ERC20: {dc.get('is_erc20','?')}"
                    )
                    if dc.get("risk_indicators"):
                        for r in dc["risk_indicators"][:3]:
                            lines.append(f"  ⚠️ {r['finding']}")

            keyboard = [
                [
                    InlineKeyboardButton(
                        "🌐 View Full Report",
                        url=f"http://127.0.0.1:8002/api/v1/investigation/graph?address={address}",
                    )
                ]
            ]
            await update.message.reply_text(
                "\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        await update.message.reply_text(f"Investigation failed: {str(e)[:100]}")


def register(app):
    app.add_handler(CommandHandler("investigate", investigate_cmd))
    app.add_handler(CommandHandler("case", investigate_cmd))
