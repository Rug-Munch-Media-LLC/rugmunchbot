"""Alert engine background jobs — price alert checks + monitor re-scans.

JobQueue entry points ``check_alerts`` and ``check_monitors``. Extracted
verbatim from the former single-module ``handlers/alerts.py``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import fmt_number
from rugmunchbot.formatters import footer_links, sep
from rugmunchbot.handlers.alerts._conditions import (
    _check_alert_condition,
    _monitor_baseline,
    _monitor_deltas,
)
from rugmunchbot.handlers.alerts._constants import DIRECTION_LABELS, MONITOR_SCAN_BUDGET
from rugmunchbot.handlers.alerts._prices import _fetch_prices_batch
from rugmunchbot.states import check_alert_rate_limit
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: check all active price alerts and fire notifications."""
    logger.info("\U0001f514 Checking price alerts...")

    active_alerts = db.get_active_alerts()
    if not active_alerts:
        logger.info("No active alerts to check.")
        return

    logger.info(f"Found {len(active_alerts)} active alert(s) to check.")
    unique_addresses = list({a["token_address"].lower() for a in active_alerts})
    current_prices = await _fetch_prices_batch(unique_addresses)

    if not current_prices:
        logger.warning("No prices fetched from DexScreener, skipping alert check.")
        return

    triggered: list[dict[str, Any]] = []
    for alert in active_alerts:
        addr = alert["token_address"].lower()
        current_price = current_prices.get(addr, 0.0)
        if current_price <= 0:
            continue
        if _check_alert_condition(current_price, alert["alert_price"], alert["alert_direction"]):
            triggered.append(alert)

    if not triggered:
        logger.info("No alerts triggered.")
        return

    logger.info(f"{len(triggered)} alert(s) triggered, sending notifications...")
    sent_count = 0
    rate_limited = 0

    for alert in triggered:
        user_id = alert["user_id"]
        if not check_alert_rate_limit(user_id):
            rate_limited += 1
            continue

        symbol = alert.get("symbol") or "???"
        addr = alert["token_address"]
        current_price = current_prices.get(addr.lower(), 0.0)
        target_price = alert["alert_price"]
        direction = alert["alert_direction"]

        change_pct = (
            abs((current_price - target_price) / target_price) * 100 if target_price > 0 else 0.0
        )

        direction_emoji = (
            "\U0001f4c8"
            if direction == "above"
            else "\U0001f4c9"
            if direction == "below"
            else "\U0001f4ca"
        )
        direction_label = DIRECTION_LABELS.get(direction, direction)

        text = (
            f"\U0001f514 <b>Price Alert Triggered!</b>\n{sep()}\n\n"
            f"<b>{symbol}</b>\n"
            f"<code>{addr}</code>\n\n"
            f"\U0001f4b0 <b>Current Price:</b> {fmt_number(current_price)}\n"
            f"\U0001f3af <b>Target Price:</b> {fmt_number(target_price)}\n"
            f"{direction_emoji} <b>Direction:</b> {direction_label}\n"
            f"\U0001f4ca <b>Change:</b> {change_pct:.1f}%\n"
            f"{footer_links()}"
        )

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "\u2705 Dismiss", callback_data=f"dismiss_alert_{alert['id']}"
                    )
                ],
            ]
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            sent_count += 1
        except httpx.HTTPError as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Failed to send alert to user {user_id}: {e}")
            continue

        db.mark_alert_triggered(alert["id"])

    logger.info(
        f"Alert check complete: {sent_count} sent, {rate_limited} rate-limited, "
        f"{len(triggered) - sent_count - rate_limited} failed"
    )


async def check_monitors(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: re-scan paid monitors and DM meaningful security deltas.

    Real-time LP/whale/price signals already flow through the webhook bridge
    (``MONITOR_EVENT_TYPES``). This job covers the properties that only change
    on-chain and are surfaced by a re-scan: tax %, ownership-renounce state,
    mint authority, LP lock %, honeypot. Deltas are pushed through the SAME
    Redis alert queue as webhook events (``enqueue_telegram_alert``) so the
    bot's ``deliver_pending_alerts`` job formats + sends the DM — no new
    delivery rail. Fail-open: one bad scan never crashes the tick.

    LIGHT PATH: each monitor is refreshed with ``scan_token(...,
    security_only=True)`` — the cheap EVM security-only recheck (GoPlus →
    honeypot.is), skipping market data, databus, GMGN/risk enrichment and
    RAG indexing. Multiple monitors on the SAME token share ONE refresh per
    tick via a local cache, so a paid-user burst of N monitors on one token
    costs one light upstream call instead of N full scans.
    """
    logger.info("\U0001f6f0\ufe0f Checking token monitors...")
    try:
        expired = db.expire_monitors()
        if expired:
            logger.info("monitors_expired count=%s", expired)
    except Exception:
        logger.warning("monitors_expire_failed", exc_info=True)

    monitors = db.get_active_monitors()
    if not monitors:
        return
    logger.info("Found %s active monitor(s) to check.", len(monitors))

    from rugmunchbot._vendor.telegram_bridge import enqueue_telegram_alert
    from rugmunchbot.scanner import scan_token

    checked = 0
    # Per-tick refresh cache: monitors on the same token share one
    # security-only refresh (keyed by chain:address).
    tick_cache: dict[str, dict[str, Any]] = {}
    for mon in monitors:
        if checked >= MONITOR_SCAN_BUDGET:
            logger.info("monitor_scan_budget_reached budget=%s", MONITOR_SCAN_BUDGET)
            break
        checked += 1
        baseline = mon.get("baseline") or {}
        addr = mon["token_address"]
        chain = mon.get("chain") or None
        cache_key = f"{(chain or 'unknown').lower()}:{addr.lower()}"
        result = tick_cache.get(cache_key)
        if result is None:
            try:
                result = await scan_token(addr, chain, security_only=True)
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
            ) as e:
                logger.warning("monitor_scan_failed token=%s err=%s", addr[:10], e)
                continue
            tick_cache[cache_key] = result
        current = _monitor_baseline(result)
        deltas = _monitor_deltas(baseline, current)
        db.update_monitor_baseline(mon["id"], current)
        if not deltas:
            continue
        payload = {
            "token_address": addr,
            "chain": result.get("chain") or chain or "unknown",
            "token_symbol": result.get("symbol") or mon.get("symbol") or "",
            "changes": deltas,
        }
        await enqueue_telegram_alert(
            user_id=mon["user_id"],
            event_type="token.monitor_change",
            payload=payload,
            event_id=f"mon:{mon['id']}:{int(time.time())}",
        )
        logger.info("monitor_change_enqueued token=%s deltas=%s", addr[:10], deltas)
    logger.info("Monitor check complete: %s scanned.", checked)
