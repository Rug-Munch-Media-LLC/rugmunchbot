"""
🔄 Buy/Sell Simulation
=======================
Simulate how a buy or sell would execute at current market conditions.
"""

from __future__ import annotations

import httpx

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi.simulate")

DEXSCREENER = "https://api.dexscreener.com/latest/dex"


async def cmd_simulate(update, ctx) -> None:
    """Handle /simulate command - simulate buy/sell."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🔄 <b>Usage:</b> <code>/simulate &lt;token_address&gt; [amount_usd]</code>\n\n"
            "Simulates a buy/sell with current liquidity and price impact.\n\n"
            "Example: <code>/simulate 0x... 100</code> (simulate $100 buy)\n"
            "Default amount: $100",
            parse_mode="HTML",
        )
        return

    address = args[0]
    amount_usd = 100.0
    if len(args) > 1:
        try:
            amount_usd = float(args[1])
        except ValueError:
            await msg.reply_text("❌ Invalid amount. Use a number like 100.", parse_mode="HTML")
            return

    await msg.chat.send_action("typing")

    try:
        client = get_shared_client(15)
        r = await client.get(f"{DEXSCREENER}/tokens/{address}")
        if r.status_code != 200:
            await msg.reply_text("❌ Could not fetch token data. Please try again in a moment.", parse_mode="HTML")
            return

        data = r.json()
        pairs = data.get("pairs", [])
        if not pairs:
            await msg.reply_text(
                "❌ No trading pairs found.\n\nEnsure the token has an active pair and try again.",
                parse_mode="HTML",
            )
            return

        p = pairs[0]
        symbol = p.get("baseToken", {}).get("symbol", "???")
        price = float(p.get("priceUsd", 0))
        liquidity = float(p.get("liquidity", {}).get("usd", 0))
        fdv = float(p.get("fdv", 0))

        if price == 0:
            await msg.reply_text(
                "❌ Token price data unavailable.\n\nCheck the address and try again.",
                parse_mode="HTML",
            )
            return

        # Calculate simulated results
        tokens_received = amount_usd / price
        price_impact_pct = (amount_usd / liquidity * 100) if liquidity > 0 else 100
        price_impact_pct = min(price_impact_pct, 100)
        estimated_slippage = price_impact_pct * 0.5  # rough estimate
        # Determine quality
        if price_impact_pct < 1:
            quality = "🟢 Low impact"
        elif price_impact_pct < 5:
            quality = "🟡 Moderate impact"
        elif price_impact_pct < 15:
            quality = "🟠 High impact"
        else:
            quality = "🔴 Extreme impact"

        lines = [
            f"🔄 <b>Trade Simulation: {symbol}</b>",
            "",
            f"💰 <b>Investment:</b> ${amount_usd:,.2f}",
            f"🪙 <b>Tokens:</b> {tokens_received:,.4f}",
            f"💵 <b>Price:</b> ${price:.8f}",
            "",
            "📊 <b>Market Impact</b>",
            f"  📉 Price Impact: <b>{price_impact_pct:.2f}%</b>",
            f"  ⚡ Est. Slippage: <b>{estimated_slippage:.2f}%</b>",
            f"  💧 Liquidity Used: <b>${amount_usd:,.0f} / ${liquidity:,.0f}</b>",
            f"  {quality}",
            "",
            f"🏷️ <b>FDV:</b> ${fdv:,.0f}",
            "",
            "<i>Not financial advice. DYOR.</i>",
        ]

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n\n<i>...truncated</i>"

        await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"Simulate failed: {e}")
        await msg.reply_text("❌ Simulation failed. Please try again in a moment.", parse_mode="HTML")
