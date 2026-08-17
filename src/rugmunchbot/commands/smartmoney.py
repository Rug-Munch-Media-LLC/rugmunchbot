"""
🧠 Smart Money Detection
=========================
Detect smart money wallet activity for a given token.
"""

from __future__ import annotations

import httpx

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, DEXSCREENER, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi.smartmoney")


async def cmd_smartmoney(update, ctx) -> None:
    """Handle /smartmoney command - detect smart money wallets."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🧠 <b>Usage:</b> <code>/smartmoney &lt;token_address&gt;</code>\n\n"
            "Detects smart money wallets that bought this token early.\n\n"
            "Example: <code>/smartmoney 0x...</code>\n\n"
            "<i>Premium feature — shows top early buyers and their P&L.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    await msg.chat.send_action("typing")

    try:
        # Try DataBus first
        smart_money_data = None
        top_holders = []

        client = get_shared_client(20)
        try:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/databus/token/{address}",
                headers=backend_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                smart_money_data = data.get("smart_money", [])
                top_holders = data.get("top_holders", [])
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.debug("RMI backend call failed: %s", exc)

        # Fallback: fetch DexScreener for basic info
        r = await client.get(f"{DEXSCREENER}/tokens/{address}")
        pair_data = {}
        if r.status_code == 200:
            pairs = r.json().get("pairs", [])
            if pairs:
                p = pairs[0]
                pair_data = {
                    "symbol": p.get("baseToken", {}).get("symbol", "???"),
                    "name": p.get("baseToken", {}).get("name", "Unknown"),
                    "price": p.get("priceUsd", 0),
                    "mcap": p.get("marketCap", 0),
                }

        symbol = pair_data.get("symbol", "???")
        name = pair_data.get("name", "Unknown")

        lines = [
            f"🧠 <b>Smart Money: {name} ({symbol})</b>",
            "",
        ]

        if smart_money_data:
            lines.append("🏆 <b>Smart Money Wallets</b>")
            for i, w in enumerate(smart_money_data[:5], 1):
                addr = w.get("address", "")
                pnl = w.get("pnl_usd", 0)
                entry = w.get("entry_price", 0)
                pnl_emoji = "📈" if pnl >= 0 else "📉"
                lines.append(f"  {i}. <code>{addr}</code> {pnl_emoji} ${pnl:,.0f}")
                if entry:
                    lines.append(f"     Entry: ${entry:.8f}")
            lines.append("")
        elif top_holders:
            lines.append("🏆 <b>Top Holders (Early Buyers)</b>")
            for i, h in enumerate(top_holders[:5], 1):
                addr = h.get("address", "")
                pct = h.get("pct", 0)
                lines.append(f"  {i}. <code>{addr}</code> - {pct:.2f}%")
            lines.append("")
        else:
            lines.append("❌ No smart money data available for this token.")
            lines.append("")

        if pair_data.get("price"):
            lines.append(f"💵 <b>Price:</b> ${float(pair_data['price']):.8f}")
        if pair_data.get("mcap"):
            lines.append(f"📊 <b>Market Cap:</b> ${float(pair_data['mcap']):,.0f}")

        lines.extend(
            [
                "",
                "<i>Smart money = wallets with >50% win rate on early entries.</i>",
            ]
        )

        text = "\n".join(lines)
        await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"SmartMoney failed: {e}")
        await msg.reply_text("❌ Could not fetch smart money data.", parse_mode="HTML")
