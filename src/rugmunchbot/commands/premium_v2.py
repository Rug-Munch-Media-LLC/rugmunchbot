"""
Premium V2 Commands — Honeypot Map, Token Laundry.
All free during beta. (LP health lives in handlers/defi.py — /lpcheck is
canonical; /lphealth is aliased to it in bot.py.)
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json, http_post_json

logger = get_logger("rmi.premium_v2")


async def _backend_get(path: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any] | None:
    body = await http_get_json(
        f"{BACKEND_URL}{path}",
        headers=backend_headers(),
        params=params or {},
        timeout=timeout,
    )
    if body is None:
        logger.debug("backend GET %s failed", path)
    return body


async def _backend_post(path: str, json: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any] | None:
    body = await http_post_json(
        f"{BACKEND_URL}{path}",
        headers=backend_headers(),
        json=json or {},
        timeout=timeout,
    )
    if body is None:
        logger.debug("backend POST %s failed", path)
    return body


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.2f}"


# ═══════════════════════════════════════════════════════════════════════════
# /honeypot — HONEYPOT MAP
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_honeypot(update, ctx) -> None:
    """Handle /honeypot — check if token is a honeypot."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🍯 <b>Honeypot Checker</b>\n\n"
            "Usage: <code>/honeypot &lt;token_address&gt; [chain]</code>\n\n"
            "Checks if a token is a honeypot (can't sell):\n"
            "• Sell tax analysis\n"
            "• Transfer restrictions\n"
            "• Blacklist functions\n"
            "• Trading cooldowns\n\n"
            "Example: <code>/honeypot 0x...</code>\n"
            "<i>Free during beta — don't get trapped.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    chain = args[1] if len(args) > 1 else "ethereum"

    await msg.chat.send_action("typing")
    data = await _backend_get(f"/api/v1/honeypot-map/check/{address}", params={"chain": chain})

    if not data:
        await msg.reply_text("❌ Honeypot check unavailable.\n\nPlease try again in a moment.", parse_mode="HTML")
        return

    is_hp = data.get("is_honeypot", False)
    risk = data.get("risk_level", "unknown")
    emoji = "🔴" if is_hp else "🟢"

    lines = [
        "🍯 <b>Honeypot Check</b>",
        f"<code>{address}</code> ({chain.title()})",
        "",
        f"{emoji} <b>{'HONEYPOT DETECTED' if is_hp else 'NOT A HONEYPOT'}</b>",
        f"Risk: {risk.title()}",
    ]

    if data.get("sell_tax") is not None:
        lines.append(f"💸 Sell Tax: {data['sell_tax']:.1f}%")
    if data.get("buy_tax") is not None:
        lines.append(f"📥 Buy Tax: {data['buy_tax']:.1f}%")
    if data.get("transfer_restricted"):
        lines.append("⚠️ Transfer restrictions detected")
    if data.get("blacklist"):
        lines.append("🚫 Blacklist functions found")
    if data.get("cooldown"):
        lines.append(f"⏱️ Trading cooldown: {data['cooldown']}s")

    if data.get("notes"):
        lines.append(f"\n💡 {data['notes']}")

    lines.append("\n<i>Red = honeypot. Green = appears safe. Always DYOR.</i>")
    await msg.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
# /laundry — TOKEN LAUNDERING DETECTION
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_laundry(update, ctx) -> None:
    """Handle /laundry — detect token laundering/tumbling."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🧼 <b>Laundry Detector</b>\n\n"
            "Usage: <code>/laundry &lt;wallet_address&gt;</code>\n\n"
            "Detects money laundering through a wallet:\n"
            "• Mixer/tumbler usage\n"
            "• Peel chain patterns\n"
            "• Cross-chain bridging to privacy coins\n"
            "• Risk score\n\n"
            "Example: <code>/laundry 0x...</code>\n"
            "<i>Free during beta — trace illicit funds.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]

    await msg.chat.send_action("typing")
    data = await _backend_post("/api/v1/laundry-matcher/scan", json={"address": address})

    if not data:
        await msg.reply_text("❌ Laundry scan unavailable.\n\nPlease try again in a moment.", parse_mode="HTML")
        return

    risk = data.get("risk_score", 0)
    emoji = "🔴" if risk >= 70 else "🟡" if risk >= 40 else "🟢"

    lines = [
        "🧼 <b>Laundry Detector</b>",
        f"<code>{address}</code>",
        "",
        f"{emoji} <b>Laundry Risk:</b> {risk}/100",
    ]

    if data.get("mixer_usage"):
        lines.append("🌀 Mixer/Tumbler detected")
    if data.get("peel_chain"):
        lines.append("📜 Peel chain pattern found")
    if data.get("privacy_bridge"):
        lines.append("🌉 Privacy coin bridge detected")
    if data.get("total_volume"):
        lines.append(f"💰 Total Volume: {_fmt_usd(data['total_volume'])}")
    if data.get("flags"):
        lines.append("")
        lines.append("<b>Flags:</b>")
        for f in data["flags"][:5]:
            lines.append(f"  ⚠️ {f}")

    lines.append("\n<i>High risk = likely laundering. Used by investigators.</i>")
    await msg.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
