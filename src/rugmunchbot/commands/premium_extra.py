"""
Additional Premium Commands — Whale Alerts, Fund Tracing.
All free during beta.

OSS note: billing/tiers are PRIVATE in the live bot. This command is a
config-gated feature flag checked through the operator's BillingProvider
(plugins/billing.py) — see ``_premium_gate``.
"""

from __future__ import annotations

from typing import Any

import httpx

from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.plugins.billing import get_billing_provider
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi.premium_extra")


async def _backend_get(
    path: str, params: dict[str, Any] | None = None, timeout: float = 15.0
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                f"{BACKEND_URL}{path}", headers=backend_headers(), params=params or {}
            )
            if r.status_code == 200:
                return r.json()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("backend GET %s failed: %s", path, exc)
    return None


async def _backend_post(
    path: str, json: dict[str, Any] | None = None, timeout: float = 20.0
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{BACKEND_URL}{path}", headers=backend_headers(), json=json or {}
            )
            if r.status_code == 200:
                return r.json()
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("backend POST %s failed: %s", path, exc)
    return None


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.2f}"


# ═══════════════════════════════════════════════════════════════════════════
# PREMIUM GATE — OSS feature flag (billing stays PRIVATE)
# ═══════════════════════════════════════════════════════════════════════════
# Same seam as commands/premium.py: the OSS build ships a single ``free`` tier
# with env quotas (BOT_FREE_SCANS_PER_WEEK / BOT_FREE_AI_MSGS_PER_WEEK) and
# delegates enforcement to the operator's BillingProvider. Default no-op
# allows everything.


async def _premium_gate(msg: Any) -> bool:
    """Gate a premium command behind the operator's BillingProvider.

    Returns True when the user may proceed. Entitlement + quota are delegated
    to plugins/billing.BillingProvider (check_entitlement / consume_quota) —
    the OSS default no-op allows all.
    """
    provider = get_billing_provider()
    uid = msg.from_user.id if msg.from_user else 0
    if not await provider.check_entitlement(uid, "premium"):
        await msg.reply_text(
            "💎 <b>Premium Feature</b>\n\n"
            "This command is gated by the operator's billing provider.\n"
            "Self-hosted deployments control access via the BillingProvider "
            "plugin (see docs/); the default allows everything.",
            parse_mode="HTML",
        )
        return False
    await provider.consume_quota(uid, "premium")
    return True


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

    if not await _premium_gate(msg):
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
        json={
            "user_id": str(msg.from_user.id),
            "address": address,
            "chain": chain,
            "min_usd": 50000,
        },
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
