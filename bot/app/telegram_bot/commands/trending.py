"""
📈 Trending Tokens — Powered by DataBus
=======================================
Fetch trending tokens across chains from the RMI backend.
"""

import json
import logging
from urllib.request import Request, urlopen

logger = logging.getLogger("rmi.trending")

BACKEND_URL = "http://rmi-backend:8000"


async def cmd_trending(update, ctx):
    """Handle /trending command."""
    await update.message.chat.send_action("typing")
    try:
        req = Request(
            f"{BACKEND_URL}/api/v1/databus/fetch",
            data=json.dumps({"data_type": "trending", "limit": 10}).encode(),
            headers={"Content-Type": "application/json", "X-RMI-Key": "rmi-internal-2026"},
        )
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tokens = data.get("data", data.get("tokens", []))[:10]

        if not tokens:
            await update.message.reply_text("📈 No trending data available right now.", parse_mode="HTML")
            return

        lines = ["<b>📈 Trending Tokens</b>\n"]
        for i, t in enumerate(tokens, 1):
            t.get("name", t.get("token_name", "?"))
            symbol = t.get("symbol", "?")
            price = t.get("price_usd", t.get("price", 0))
            change = t.get("price_change_24h", t.get("change_24h", 0))
            emoji = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
            lines.append(f"{i}. <b>{symbol}</b> — ${price:.6f} {emoji} {change:+.1f}%")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n\n<i>…more on rugmunch.io</i>"

        await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Trending failed: {e}")
        await update.message.reply_text("❌ Could not fetch trending data.", parse_mode="HTML")


async def cmd_launches(update, ctx):
    """Handle /launches command — new token launches."""
    await update.message.chat.send_action("typing")
    try:
        req = Request(
            f"{BACKEND_URL}/api/v1/databus/fetch",
            data=json.dumps({"data_type": "token_launches", "limit": 10}).encode(),
            headers={"Content-Type": "application/json", "X-RMI-Key": "rmi-internal-2026"},
        )
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tokens = data.get("data", data.get("tokens", []))[:10]

        if not tokens:
            await update.message.reply_text("🚀 No new launches detected recently.", parse_mode="HTML")
            return

        lines = ["<b>🚀 New Token Launches</b>\n"]
        for i, t in enumerate(tokens, 1):
            t.get("name", t.get("token_name", "?"))
            symbol = t.get("symbol", "?")
            chain = t.get("chain", "?")
            age = t.get("age_minutes", t.get("age", 0))
            age_str = f"{age:.0f}m" if age < 60 else f"{age / 60:.1f}h"
            lines.append(f"{i}. <b>{symbol}</b> on {chain} — {age_str} old")

        text = "\n".join(lines)
        await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Launches failed: {e}")
        await update.message.reply_text("❌ Could not fetch launch data.", parse_mode="HTML")
