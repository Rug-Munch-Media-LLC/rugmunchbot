#!/usr/bin/env python3
"""
V3 alert system — /alerts on|off [category], inline toggles, and the
alert delivery polling job (max 5 alerts/hour/user, dedupe per token).

Categories: security, whale, price. Security + whale require Scout+ tier.
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import httpx
from config import ALERT_MAX_PER_HOUR, ALERT_POLL_URL, RMI_AUTH_TOKEN
from handlers.menu import pricing_kb
from services.formatting import esc, format_alert, sep
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORIES = ("security", "whale", "price")
CATEGORY_LABELS = {
    "security": "🛡️ Security",
    "whale": "🐋 Whale",
    "price": "💰 Price",
}
# Security + whale require Scout+; price requires Scout+ (existing behavior).
PREMIUM_CATEGORIES = ("security", "whale", "price")

# In-memory per-user hourly cap (TTL fallback per design; Redis preferred later).
_alert_hour_counts: dict[int, list[float]] = {}


def _alert_rate_limited(user_id: int) -> bool:
    now = time.time()
    stamps = [t for t in _alert_hour_counts.get(user_id, []) if now - t < 3600]
    if len(stamps) >= ALERT_MAX_PER_HOUR:
        _alert_hour_counts[user_id] = stamps
        return True
    stamps.append(now)
    _alert_hour_counts[user_id] = stamps
    return False


def _can_subscribe(user_id: int, category: str) -> bool:
    if category not in PREMIUM_CATEGORIES:
        return True
    return bool(db.get_user_tier(user_id) != "free")


def alerts_status_text(user_id: int) -> str:
    subs = {s["category"]: s["is_active"] for s in db.get_alert_subscriptions(user_id)}
    lines = ["🔔 <b>Alert Subscriptions</b>", sep(), ""]
    for cat in CATEGORIES:
        state = "🔔 ON" if subs.get(cat) else "🔕 OFF"
        lines.append(f"{CATEGORY_LABELS[cat]}: <b>{state}</b>")
    lines.extend(
        [
            "",
            "Usage: /alerts on <code>security</code> · /alerts off <code>price</code>",
            "Categories: security · whale · price",
        ]
    )
    return "\n".join(lines)


def alerts_kb(user_id: int) -> InlineKeyboardMarkup:
    subs = {s["category"]: s["is_active"] for s in db.get_alert_subscriptions(user_id)}
    rows = []
    for cat in CATEGORIES:
        active = bool(subs.get(cat))
        label = f"{'🔔' if active else '🔕'} {CATEGORY_LABELS[cat]} — {'ON' if active else 'OFF'}"
        rows.append([InlineKeyboardButton(label, callback_data=f"alerts_{cat}_toggle")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def _alert_kb(alert: dict) -> InlineKeyboardMarkup | None:
    address = alert.get("token_address") or alert.get("address") or ""
    chain = alert.get("chain") or "solana"
    if not address:
        return None
    from handlers.menu import chart_url

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔍 Scan", callback_data=f"scan_{address}_{chain}"),
                InlineKeyboardButton("🤖 AI", callback_data=f"ai_{address}_{chain}"),
                InlineKeyboardButton("📊 Chart", url=chart_url(address, chain)),
            ]
        ]
    )


# ── /alerts ──


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None or update.effective_chat is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    args = ctx.args or []
    if args:
        action = args[0].lower()
        if action in ("on", "off") and len(args) > 1:
            category = args[1].lower()
            if category not in CATEGORIES:
                await update.message.reply_text(
                    f"❌ Unknown category: <code>{esc(category)}</code>\n\n"
                    f"Available: {', '.join(CATEGORIES)}",
                    parse_mode=ParseMode.HTML,
                )
                return
            if action == "on":
                if not _can_subscribe(user.id, category):
                    await update.message.reply_text(
                        f"🔒 <b>{CATEGORY_LABELS[category]} alerts require Scout+ tier.</b>\n\n"
                        "Upgrade to unlock:",
                        parse_mode=ParseMode.HTML,
                        reply_markup=pricing_kb(),
                    )
                    return
                db.set_alert_subscription(user.id, category, update.effective_chat.id, True)
                await update.message.reply_text(
                    f"✅ <b>Alerts enabled:</b> {CATEGORY_LABELS[category]}\n\n"
                    "You'll be notified here when matching alerts fire.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=alerts_kb(user.id),
                )
            else:
                db.set_alert_subscription(user.id, category, update.effective_chat.id, False)
                await update.message.reply_text(
                    f"🔕 <b>Alerts disabled:</b> {CATEGORY_LABELS[category]}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=alerts_kb(user.id),
                )
            return
        if action in ("on", "off"):
            active = action == "on"
            for cat in CATEGORIES:
                db.set_alert_subscription(user.id, cat, update.effective_chat.id, active)
            verb = "enabled" if active else "disabled"
            await update.message.reply_text(
                f"{'✅' if active else '🔕'} <b>All alerts {verb}.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=alerts_kb(user.id),
            )
            return

    await update.message.reply_text(
        alerts_status_text(user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=alerts_kb(user.id),
    )


async def display_alerts(query, user_id: int) -> None:
    """Callback entry (menu_alerts) — edits the tapped message in place."""
    await query.edit_message_text(
        alerts_status_text(user_id),
        parse_mode=ParseMode.HTML,
        reply_markup=alerts_kb(user_id),
    )


async def handle_alerts_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle subscription from the alerts keyboard (alerts_{cat}_toggle)."""
    query = update.callback_query
    if query is None:
        return
    user = query.from_user
    parts = (query.data or "").split("_")
    if len(parts) != 3 or parts[0] != "alerts" or parts[2] != "toggle":
        return
    category = parts[1]
    if category not in CATEGORIES:
        return

    subs = {s["category"]: s["is_active"] for s in db.get_alert_subscriptions(user.id)}
    current = bool(subs.get(category))
    if not current and not _can_subscribe(user.id, category):
        await query.edit_message_text(
            f"🔒 <b>{CATEGORY_LABELS[category]} alerts require Scout+ tier.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    channel_id = query.message.chat.id if query.message else user.id
    db.set_alert_subscription(user.id, category, channel_id, active=not current)
    await query.edit_message_text(
        alerts_status_text(user.id),
        parse_mode=ParseMode.HTML,
        reply_markup=alerts_kb(user.id),
    )


# ── Alert delivery polling job ──


async def fetch_pending_alerts() -> list[dict]:
    """Poll the backend alert queue; return [] on failure (job stays silent)."""
    try:
        headers = {"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {}
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(ALERT_POLL_URL, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("alerts") or data.get("items") or []
    except Exception as exc:
        logger.debug("alert poll failed: %s", exc)
    return []


async def alert_delivery_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Poll backend alert queue and fan out to subscribers (5/hour/user cap)."""
    alerts = await fetch_pending_alerts()
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        category = str(alert.get("category") or alert.get("alert_type") or "security").lower()
        if category not in CATEGORIES:
            category = "security"
        token = alert.get("token_address") or alert.get("address") or ""
        alert_type = str(alert.get("alert_type") or category)
        if token and db.was_alert_sent(token, alert_type):
            continue  # dedupe window

        subscribers = db.get_alert_subscribers(category)
        sent_any = False
        for sub in subscribers:
            if _alert_rate_limited(sub["user_id"]):
                continue
            try:
                await context.bot.send_message(
                    sub["channel_id"],
                    format_alert(alert),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_alert_kb(alert),
                    disable_web_page_preview=True,
                )
                sent_any = True
            except Exception as exc:
                if "Forbidden" in str(exc) or "blocked" in str(exc).lower():
                    db.set_alert_subscription(sub["user_id"], category, sub["channel_id"], False)
                    logger.info(
                        "auto-unsubscribed %s from %s (bot blocked)", sub["user_id"], category
                    )
                logger.debug("alert delivery to %s failed: %s", sub["user_id"], exc)
        if sent_any and token:
            db.log_alert_sent(token, alert_type)
