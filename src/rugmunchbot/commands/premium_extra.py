"""
Additional Premium Commands — Whale Alerts, Fund Tracing.
All free during beta.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json, http_post_json

logger = get_logger("rmi.premium_extra")


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


async def cmd_whale(update, ctx) -> None:
    """Handle /whale — subscribe to whale movement alerts for a token."""
    msg = update.message
    args = ctx.args

    if not args or args[0] == "help":
        await msg.reply_text(
            "🐋 <b>Whale Alerts</b>\n\n"
            "Subscribe to large movement alerts for any token:\n\n"
            "<b>Usage:</b>\n"
            "  <code>/whale &lt;token_address&gt;</code> — subscribe\n"
            "  <code>/whale list</code> — view subscriptions\n"
            "  <code>/whale remove &lt;id&gt;</code> — remove subscription\n\n"
            "Alerts fire when:\n"
            "• Transfer >$50K USD\n"
            "• Top 10 wallet moves >5% of supply\n"
            "• Liquidity added/removed >$100K\n\n"
            "<i>Free during beta — never miss a whale move.</i>",
            parse_mode="HTML",
        )
        return

    if args[0] == "list":
        data = await _backend_get(f"/api/v1/whale-alerts/subscriptions/{msg.from_user.id}")
        subs = data.get("subscriptions", []) if data else []
        if not subs:
            await msg.reply_text(
                "📭 <b>No whale alert subscriptions.</b>\n\nUse <code>/whale &lt;address&gt;</code> to subscribe.",
                parse_mode="HTML",
            )
            return
        lines = [f"🐋 <b>Your Whale Alerts ({len(subs)})</b>", ""]
        for s in subs:
            addr = s.get("address", "")
            sid = s.get("id", "")
            lines.append(f"• <code>{addr}</code>")
            lines.append(f"  <code>/whale remove {sid}</code> to unsubscribe")
        await msg.reply_text("\n".join(lines), parse_mode="HTML")
        return

    if args[0] == "remove" and len(args) > 1:
        sid = args[1]
        data = await _backend_post(f"/api/v1/whale-alerts/unsubscribe/{sid}")
        if data:
            await msg.reply_text("✅ Whale alert removed.", parse_mode="HTML")
        else:
            await msg.reply_text("❌ Could not remove subscription.", parse_mode="HTML")
        return

    # Subscribe
    address = args[0]
    chain = args[1] if len(args) > 1 else "ethereum"

    data = await _backend_post(
        "/api/v1/whale-alerts/subscribe",
        json={"user_id": str(msg.from_user.id), "address": address, "chain": chain, "min_usd": 50000},
    )

    if data:
        await msg.reply_text(
            f"🐋 <b>Whale Alert Subscribed!</b>\n\n"
            f"Watching <code>{address}</code> ({chain.title()})\n\n"
            f"You'll be notified when:\n"
            f"• Transfer >$50K USD\n"
            f"• Top wallet moves >5% of supply\n"
            f"• Liquidity change >$100K\n\n"
            f"Manage: <code>/whale list</code>",
            parse_mode="HTML",
        )
    else:
        await msg.reply_text(
            "❌ Could not subscribe. The token may be invalid or the service is unavailable.",
            parse_mode="HTML",
        )
