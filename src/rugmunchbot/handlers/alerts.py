#!/usr/bin/env python3
"""
RugMunchBot -- Alert engine + /alert and /alerts commands.

Background price alert checker and alert management commands.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    TIERS,
    backend_headers,
    fmt_number,
)
from app.domains.telegram.rugmunchbot.data_resolver import backend_get_json, resolve_with_fallback
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    sep,
)
from app.domains.telegram.rugmunchbot.handlers._messages import failure_card, usage_hint
from app.domains.telegram.rugmunchbot.keyboards import back_kb, pricing_kb
from app.domains.telegram.rugmunchbot.states import check_alert_rate_limit
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client, http_get_json

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

# --- Alert engine ---

DIRECTION_LABELS = {
    "above": "\U0001f4c8 above",
    "below": "\U0001f4c9 below",
    "change_5": "\U0001f4ca 5% change",
    "any": "\U0001f504 significant change",
}


async def _fetch_prices_batch(addresses: list[str]) -> dict[str, float]:
    """Fetch current USD prices for a list of token addresses.

    Own-data first: the backend scanner cache (``/api/v2/scanner/token/price``,
    populated by our own scanners from ClickHouse/DataBus) is the primary
    source; DexScreener is used only as an external fallback for addresses
    the cache does not cover. DATA_SOURCE is logged per batch so reliance
    on the external provider can be tracked.
    """
    prices: dict[str, float] = {}
    if not addresses:
        return prices
    normalized = list({a.lower() for a in addresses})

    async def _own_data() -> dict[str, float]:
        """Primary: scanner price cache for every address (own data)."""
        found: dict[str, float] = {}
        for addr in normalized:
            chain = detect_chain(addr)
            if chain == "unknown":
                continue
            body = await backend_get_json(
                f"/api/v2/scanner/token/price/{chain}/{addr}",
                timeout_secs=4,
            )
            if body and body.get("cached") and body.get("data"):
                price = float(body["data"].get("price_usd") or 0)
                if price > 0:
                    found[addr] = price
        return found

    async def _external() -> dict[str, float]:
        """Fallback: DexScreener batch for any address still missing."""
        missing = [a for a in normalized if a not in prices]
        ext: dict[str, float] = {}
        if not missing:
            return ext
        batch = ",".join(missing)
        data = await http_get_json(f"{DEXSCREENER}/tokens/{batch}", timeout=10)
        if data is None:
            logger.warning("Batch price fetch error")
            return ext
        pairs = data.get("pairs", [])
        for pair in pairs:
            addr = pair.get("baseToken", {}).get("address", "").lower()
            price = float(pair.get("priceUsd", 0))
            if addr and price > 0 and addr not in ext:
                ext[addr] = price
        return ext

    # Own data first: try the scanner cache for the whole batch. If ANY address
    # resolves from own data, keep those hits and only fill the rest externally
    # (the resolver logs DATA_SOURCE=own_data when at least one hit came from
    # our cache). If the cache is completely cold, the resolver falls back to
    # DexScreener for the whole batch and logs DATA_SOURCE=fallback_external.
    prices = await resolve_with_fallback(
        "alert_price_batch",
        _own_data,
        _external,
        timeout_secs=8,
        is_valid=lambda d: bool(d),
    )
    if len(prices) < len(normalized):
        ext = await _external()
        prices.update(ext)
    return prices


def _check_alert_condition(current_price: float, alert_price: float, direction: str) -> bool:
    if current_price <= 0 or alert_price <= 0:
        return False
    if direction == "above":
        return current_price >= alert_price
    elif direction == "below":
        return current_price <= alert_price
    elif direction == "change_5":
        return abs((current_price - alert_price) / alert_price) * 100 >= 5.0
    elif direction == "any":
        return abs((current_price - alert_price) / alert_price) * 100 >= 2.0
    return False


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

        change_pct = abs((current_price - target_price) / target_price) * 100 if target_price > 0 else 0.0

        direction_emoji = (
            "\U0001f4c8" if direction == "above" else "\U0001f4c9" if direction == "below" else "\U0001f4ca"
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
                [InlineKeyboardButton("\u2705 Dismiss", callback_data=f"dismiss_alert_{alert['id']}")],
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


# --- Alert commands ---


# Real-time event types a user alert subscribes to (delivered as push alerts
# through the intelligent webhook bridge: provider → engine → Redis queue →
# bot deliver_pending_alerts job → Telegram DM).
ALERT_EVENT_TYPES = [
    "token.price_change",
    "token.whale_move",
    "token.liquidity_change",
]


# Event types the Stars-paid Monitor subscribes to. These are the on-chain
# signals the webhook engine emits per token (provider → engine → Redis queue
# → deliver_pending_alerts → DM): LP add/remove (``token.liquidity_change``),
# large/deployer wallet moves (``token.whale_move``) and price swings
# (``token.price_change``). Tax/ownership/mint changes are covered by the
# periodic ``check_monitors`` re-scan delta job (those properties only change
# on-chain, and the scanner cache surfaces them). ``transaction.enhanced``
# (Helius-style) is intentionally NOT subscribed here: the engine routes it by
# wallet_address, and the bot bridge only filters by token.
MONITOR_EVENT_TYPES = [
    "token.liquidity_change",
    "token.whale_move",
    "token.price_change",
]

# How often the tax/ownership/mint delta job re-scans active monitors.
MONITOR_CHECK_INTERVAL = 1800  # seconds (30 min)
# Hard cap on monitors re-scanned per tick so a paid-user burst never stalls
# the JobQueue (unscanned monitors are picked up on the next tick).
MONITOR_SCAN_BUDGET = 25


async def _subscribe_backend_alerts(user_id: int, token_address: str, event_types: list[str] | None = None) -> bool:
    """Register the user's chat for real webhook events on a token.

    Fail-open: local price alerts keep working even if the backend
    subscription cannot be created (e.g. backend down).
    """
    try:
        client = get_shared_client(10)
        resp = await client.post(
            f"{BACKEND_URL}/api/v1/scanner/webhooks/telegram",
            json={
                "user_id": user_id,
                "event_types": event_types or ALERT_EVENT_TYPES,
                "token_addresses": [token_address],
            },
            headers=backend_headers(),
            timeout=10,
        )
        if resp.status_code in (200, 201):
            logger.info("alert_subscribed_backend user=%s token=%s", user_id, token_address[:10])
            return True
        logger.warning(
            "alert_backend_subscribe_failed user=%s status=%s body=%s",
            user_id,
            resp.status_code,
            resp.text[:200],
        )
        return False
    except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
        logger.warning(f"alert_backend_subscribe_error user={user_id}: {e}")
        return False


async def _subscribe_backend_monitor(user_id: int, token_address: str) -> bool:
    """Subscribe a paid monitor to the live on-chain event stream.

    Reuses the same internal-telegram webhook bridge as price alerts, with
    the monitor event set (LP / whale / price). Fail-open like the alert
    path — the periodic ``check_monitors`` delta job still runs.
    """
    return await _subscribe_backend_alerts(user_id, token_address, event_types=MONITOR_EVENT_TYPES)


async def _unsubscribe_backend_monitor(user_id: int, token_address: str) -> bool:
    """Remove the monitor's internal webhook subscription for a token."""
    try:
        client = get_shared_client(10)
        resp = await client.request(
            "DELETE",
            f"{BACKEND_URL}/api/v1/scanner/webhooks/telegram",
            json={
                "user_id": user_id,
                "event_types": MONITOR_EVENT_TYPES,
                "token_addresses": [token_address],
            },
            headers=backend_headers(),
            timeout=10,
        )
        if resp.status_code in (200, 204):
            logger.info("monitor_unsubscribed_backend user=%s token=%s", user_id, token_address[:10])
            return True
        logger.warning(
            "monitor_backend_unsubscribe_failed user=%s status=%s body=%s",
            user_id,
            resp.status_code,
            resp.text[:200],
        )
        return False
    except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
        logger.warning(f"monitor_backend_unsubscribe_error user={user_id}: {e}")
        return False


# ── Monitor baseline / delta (tax / ownership / LP / mint changes) ─────────


def _monitor_baseline(result: dict[str, Any]) -> dict[str, Any]:
    """Project a scan result onto the security properties a monitor watches.

    Mirrors the fields the scanner populates (``tools/scanner.scan_token``):
    tax fractions, ownership-renounced flag, mint authority, LP lock fraction
    and honeypot state. Absent values stay None — never fabricated.
    """
    return {
        "buy_tax": result.get("buy_tax"),
        "sell_tax": result.get("sell_tax"),
        "owner_renounced": result.get("owner_renounced"),
        "mintable": result.get("mintable"),
        "lp_locked": result.get("lp_locked"),
        "honeypot": result.get("honeypot"),
    }


def _pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (ValueError, TypeError):
        return "unknown"


def _monitor_deltas(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Return human-readable change lines between two security snapshots."""
    changes: list[str] = []
    for field in ("buy_tax", "sell_tax"):
        before, after = baseline.get(field), current.get(field)
        if before is None or after is None:
            continue
        try:
            if abs(float(after) - float(before)) >= 0.01:
                changes.append(f"{field.replace('_', ' ').title()}: {_pct(before)} \u2192 {_pct(after)}")
        except (ValueError, TypeError):
            continue
    for field, label in (
        ("owner_renounced", "Ownership renounced"),
        ("mintable", "Mint authority"),
        ("honeypot", "Honeypot"),
    ):
        before, after = baseline.get(field), current.get(field)
        if before is None or after is None:
            continue
        if bool(before) != bool(after):
            changes.append(f"{label}: {bool(before)} \u2192 {bool(after)}")
    before_lp, after_lp = baseline.get("lp_locked"), current.get("lp_locked")
    if before_lp is not None and after_lp is not None:
        try:
            if float(after_lp) <= float(before_lp) - 0.2:
                changes.append(f"LP locked: {_pct(before_lp)} \u2192 {_pct(after_lp)}")
        except (ValueError, TypeError) as e:
            logger.debug("lp delta parse failed: %s", e)
    return changes


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

    from app.domains.scanner.webhooks.telegram_bridge import enqueue_telegram_alert
    from app.domains.telegram.rugmunchbot.scanner import scan_token

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
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
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
        w for w in items if w.get("alert_price") and not w.get("is_active") and w.get("alert_triggered_at")
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
            text += f"  \u2022 <b>{a.get('symbol', '???')}</b> {arrow} {fmt_number(a['alert_price'])}\n"
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

    direction_raw = (ctx.args or [])[0].upper()
    addr = (ctx.args or [])[1]
    price_str = (ctx.args or [])[2] if len(ctx.args or []) > 2 else None

    valid_directions = {"ABOVE", "BELOW", "CHANGE_5", "ANY"}
    if direction_raw not in valid_directions:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            failure_card(
                "Invalid Direction",
                f"Choose one of: {', '.join(d.lower() for d in valid_directions)}",
                "Example: /alert ABOVE <code>address</code> 0.005",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    if direction_raw in ("ABOVE", "BELOW") and not price_str:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            failure_card(
                "Missing Target Price",
                "ABOVE/BELOW alerts need a target price.",
                "Example: /alert ABOVE <code>address</code> 0.005",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    chain = detect_chain(addr)
    if chain == "unknown":
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            failure_card(
                "Chain Not Detected",
                "Ensure it's a valid EVM (0x...) or Solana address.",
                "Example: /alert BELOW EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 0.85",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    alert_price: float | None = None
    if price_str:
        try:
            alert_price = float(price_str)
            if alert_price <= 0:
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                failure_card(
                    "Invalid Price",
                    "Use a positive number (e.g., 0.005, 1.50, 42000).",
                    "Example: /alert ABOVE <code>address</code> 1.50",
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    existing = db.get_watchlist(user.id)
    existing_entry = next(
        (w for w in existing if w["token_address"].lower() == addr.lower()),
        None,
    )

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
            user.id, addr, chain, symbol=None, alert_price=alert_price, alert_dir=direction_raw.lower()
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
