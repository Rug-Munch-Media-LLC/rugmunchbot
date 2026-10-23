"""Alert commands — /alerts (list) and /alert (set/update a price alert).

Extracted verbatim from the former single-module ``handlers/alerts.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import TIERS, fmt_number
from rugmunchbot.formatters import footer_links, sep
from rugmunchbot.handlers._messages import failure_card, usage_hint
from rugmunchbot.handlers.alerts._backend_subs import _subscribe_backend_alerts
from rugmunchbot.handlers.alerts._delete import cmd_alertdel
from rugmunchbot.handlers.alerts._manage import (
    active_alert_count,
    is_duplicate,
    parse_alert_args,
    quota_error,
)
from rugmunchbot.keyboards import back_kb, pricing_kb
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """View active and triggered alerts."""
    user = update.effective_user
    if user is None:
        return
    tier = db.get_user_tier(user.id)
    if tier in ("free",):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\U0001f514 <b>Price Alerts</b>\n\n"
            f"Get notified when tokens hit your target price.\n\n"
            f"\u26a0\ufe0f <b>Requires Scout tier</b> or higher.\n"
            f"You're on {TIERS[tier]['emoji']} {TIERS[tier]['name']}.\n\n"
            f"\U0001f6f0\ufe0f <b>Monitor (250\u2605/30d)</b> — live LP, whale "
            f"and tax/ownership/mint change alerts on any token.\n"
            f"\U0001f52c <b>Deep Scan (250\u2605)</b> — full forensics on demand.\n"
            f"\U0001f4b0 <b>Core /scan + chat stay free.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    items = db.get_watchlist_for_user(user.id, include_triggered=True)
    active_alerts = [w for w in items if w.get("alert_price") and w.get("is_active")]
    triggered_alerts = [
        w
        for w in items
        if w.get("alert_price") and not w.get("is_active") and w.get("alert_triggered_at")
    ]

    if not active_alerts and not triggered_alerts:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f514 <b>Price Alerts</b>\n\n"
            "No alerts set yet.\n\n"
            "To set an alert:\n"
            "  /alert ABOVE <code>address</code> 0.005\n"
            "  /alert BELOW <code>address</code> 0.85\n"
            "  /alert CHANGE_5 <code>address</code>\n"
            "  /alert ANY <code>address</code> 0.01",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    text = f"\U0001f514 <b>Alerts</b> ({TIERS[tier]['emoji']} {TIERS[tier]['name']})\n{sep()}\n\n"

    if active_alerts:
        text += "<b>\U0001f7e2 Active</b>\n"
        for a in active_alerts:
            arrow = "\u2191" if a.get("alert_direction") == "above" else "\u2193"
            text += (
                f"  \u2022 <b>{a.get('symbol', '???')}</b> {arrow} {fmt_number(a['alert_price'])}\n"
            )
        text += "\n"

    if triggered_alerts:
        text += "<b>\U0001f534 Triggered (recent)</b>\n"
        for a in triggered_alerts[:5]:
            ts_str = ""
            if a.get("alert_triggered_at"):
                try:
                    ts = a["alert_triggered_at"]
                    if "T" in ts:
                        ts_str = f" \u2014 {ts[:10]}"
                except (ConnectionError, OSError, TimeoutError) as e:
                    logger.warning("alert triggered ts parse failed: %s", e)
            text += f"  \u2022 <b>{a.get('symbol', '???')}</b> \U0001f514 {fmt_number(a['alert_price'])}{ts_str}\n"
        if len(triggered_alerts) > 5:
            text += f"  ... and {len(triggered_alerts) - 5} more\n"

    text += "\nSet new: /alert ABOVE <code>addr</code> <price>"
    text += "\nRemove: /alert delete <code>addr</code>"
    text += footer_links()
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a price alert: /alert <ABOVE|BELOW> <address> <price>"""
    user = update.effective_user
    if user is None:
        return

    tier = db.get_user_tier(user.id)
    if tier in ("free",):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f514 <b>Price Alerts</b>\n\n"
            "Price alerts require <b>Scout tier</b> or higher.\n\n"
            "\U0001f6f0\ufe0f <b>Monitor (250\u2605/30d)</b> \u2014 live LP/whale + "
            "tax/ownership/mint change alerts on any token.\n"
            "\U0001f52c <b>Deep Scan (250\u2605)</b> \u2014 full forensics on demand.\n"
            "\U0001f4b0 <b>Core /scan + chat stay free.</b>\n\n"
            "Run /monitor <code>address</code> to start a monitor.",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    if (ctx.args or [""])[0].lower() == "delete":
        await cmd_alertdel(update, ctx)
        return

    if len(ctx.args or []) < 2:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f514 <b>Set Price Alert</b>\n\n"
            + usage_hint(
                "alert",
                "ABOVE|BELOW|CHANGE_5|ANY address [price]",
                "ABOVE 0x6982508145454Ce325dDbE47a25d4ec3d2311933 0.005",
                examples=(
                    "BELOW EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 0.85",
                    "CHANGE_5 0x6982508145454Ce325dDbE47a25d4ec3d2311933 — 5% move in either direction",
                    "ANY 0x6982508145454Ce325dDbE47a25d4ec3d2311933 — alert on 2%+ change",
                ),
                extra="<i>Add the token to your watchlist first with /watch</i>",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    spec, err = parse_alert_args(list(ctx.args or []))
    if spec is None:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            failure_card(
                "Invalid Alert",
                err or "Could not parse that alert.",
                "Example: /alert ABOVE <code>address</code> 0.005",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    addr, chain, alert_price = spec.address, spec.chain, spec.price
    direction_raw = spec.direction.upper()
    entries = db.get_watchlist_for_user(user.id, include_triggered=True)
    existing_entry = next(
        (w for w in entries if str(w["token_address"]).lower() == addr.lower()),
        None,
    )
    if existing_entry is None:
        limit_err = quota_error(tier, active_alert_count(entries))
        if limit_err:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                failure_card(
                    "Alert Limit Reached",
                    limit_err,
                    "Use /alerts to review, /alert delete to remove one.",
                ),
                parse_mode=ParseMode.HTML,
            )
            return
    elif is_duplicate(existing_entry, spec):
        # Dedupe: the exact same rule is already armed — no DB write.
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2139\ufe0f <b>Alert Already Active</b>\n\n"
            f"Token: <code>{addr}</code>\n"
            f"Rule: {spec.direction} {fmt_number(alert_price) if alert_price else 'any move'}\n\n"
            f"Nothing changed. Use /alert delete <code>{addr}</code> to remove it.",
            parse_mode=ParseMode.HTML,
        )
        return

    if existing_entry:
        conn = db.get_db()
        conn.execute(
            "UPDATE watchlists SET alert_price = ?, alert_direction = ?, is_active = 1, alert_triggered_at = NULL WHERE id = ?",
            (alert_price, direction_raw.lower(), existing_entry["id"]),
        )
        conn.commit()
        conn.close()
        await _subscribe_backend_alerts(user.id, addr)
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 <b>Alert Updated</b>\n\n"
            f"Token: <code>{addr}</code>\n"
            f"Rule: {direction_raw.lower()} {fmt_number(alert_price) if alert_price else 'any move'}\n\n"
            f"Use /alerts to see all your active alerts.",
            parse_mode=ParseMode.HTML,
        )
    else:
        ok = db.add_watchlist(
            user.id,
            addr,
            chain,
            symbol=None,
            alert_price=alert_price,
            alert_dir=direction_raw.lower(),
        )
        if ok:
            await _subscribe_backend_alerts(user.id, addr)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                f"\u2705 <b>Alert Set</b>\n\n"
                f"Token: <code>{addr}</code>\n"
                f"Rule: {direction_raw.lower()} {fmt_number(alert_price) if alert_price else 'any move'}\n\n"
                f"You'll be notified when this condition is met.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                failure_card(
                    "Alert Not Set",
                    "Watchlist limit reached or already exists.",
                    "Try /watchlist to manage your saved tokens.",
                ),
                parse_mode=ParseMode.HTML,
            )
