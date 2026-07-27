"""Telegram /scan command — v2 using Rug Muncher Scanner via port 8002."""

# ⚠️ DEAD CODE — NOT WIRED INTO MAIN BOT
# This module is NOT imported or registered by bot.py.
# Kept as reference for future migration to rmi-backend.
# DO NOT add production credentials to this file.

import logging

import httpx
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

SCANNER_URL = "http://127.0.0.1:8002/api/v1/scan"


async def cmd_scan_v2(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Scan a token or wallet using the new Rug Muncher Scanner."""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /scan2 <address> [chain]\nExample: /scan2 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
        )
        return

    address = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else "ethereum"
    tier = ctx.args[2] if len(ctx.args) > 2 else "free"

    msg = await update.message.reply_text(
        f"Scanning {address[:8]}... on {chain} tier={tier}"
    )

    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                SCANNER_URL, json={"address": address, "chain": chain, "tier": tier}
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("success"):
                    result = data["data"]
                    text = format_scan_v2(result)
                    await msg.edit_text(text, parse_mode="HTML")
                else:
                    await msg.edit_text(f"Scan failed: {data.get('error', 'unknown')}")
            else:
                await msg.edit_text(f"HTTP {r.status_code}")
    except Exception as e:
        await msg.edit_text(f"Scanner unavailable: {e}\nTry /scan instead")


def format_scan_v2(r: dict) -> str:
    """Format scanner result for Telegram HTML."""
    score = r.get("risk_score", 50)
    level = r.get("risk_level", "unknown").upper()
    addr = r.get("address", "")[:10] + "..."
    chain = r.get("chain", "ethereum")
    stype = r.get("scan_type", "token")

    emoji = (
        "🟢"
        if score < 20
        else "🟡"
        if score < 40
        else "🟠"
        if score < 60
        else "🔴"
        if score < 80
        else "🚨"
    )

    lines = [
        f"<b>{emoji} {level} ({score}/100)</b>",
        f"<code>{addr}</code> on {chain}",
        f"Type: {stype}",
    ]

    if r.get("summary"):
        lines.append(f"\n{r['summary']}")

    breakdown = r.get("score_breakdown", {})
    if breakdown:
        lines.append("\n<b>Risk Breakdown:</b>")
        for key, item in list(breakdown.items())[:5]:
            s = item.get("score", 0)
            w = item.get("weight", "med")
            reason = item.get("reason", "")[:60]
            icon = "🔴" if s >= 70 else "🟠" if s >= 50 else "🟡"
            lines.append(f"{icon} {key.replace('_', ' ')}: {s}/100 ({w})")

    bi = r.get("basic_info", {})
    lines.append("\n<b>Details:</b>")
    lines.append(f"Balance: {bi.get('balance', 0):.4f} | Txs: {bi.get('tx_count', 0)}")

    sources = r.get("sources_used", [])
    if sources:
        lines.append(f"Sources: {', '.join(sources)}")

    return "\n".join(lines)
