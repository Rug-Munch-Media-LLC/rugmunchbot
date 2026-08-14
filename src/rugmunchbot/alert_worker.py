#!/usr/bin/env python3
"""
RugMunchBot — intelligent webhook alert delivery worker.

Background job (registered in bot.py, runs every 30s like the broadcast
worker) that picks up intelligent webhook events queued in Redis by the
backend (the backend telegram bridge) and pushes them as formatted Telegram
alerts to the subscribing user.

Queue contract — Redis LIST ``bot:alerts:queue``, JSON items::

    {
      "type": "token.whale_move" | "token.price_change" | ...,
      "user_id": int,             # Telegram chat to deliver to
      "event_id": str,            # dedupe key
      "payload": {...},           # provider event payload
      "created_at": str           # ISO timestamp
    }

Delivery mirrors ``deliver_pending_broadcasts``: pop FIFO → format → send,
with a per-user rate limit (1 alert / 5 min, shared with price alerts) and
a bounded in-memory dedupe set. Fails open: Redis/DB issues skip the tick.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from rugmunchbot.config import fmt_number
from rugmunchbot.formatters import footer_links, sep, short_addr
from rugmunchbot.states import check_alert_rate_limit
from rugmunchbot.support.logging import get_logger
from rugmunchbot.support.redis import get_redis_async

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

ALERT_QUEUE_KEY = "bot:alerts:queue"
SEND_DELAY = 0.05  # seconds between sends (Telegram rate-limit politeness)

# Bounded dedupe: drop repeats of the same event_id seen within this many
# most-recent events (the backend may dispatch the same event to multiple
# overlapping subscriptions).
_RECENT_EVENT_IDS: set[str] = set()
_MAX_RECENT_EVENTS = 500

# ── Queue helpers ──────────────────────────────────────────────────────


async def pop_alert() -> dict[str, Any] | None:
    """Pop the oldest queued alert (FIFO). Returns None when empty."""
    r = get_redis_async()
    if r is None:
        return None
    try:
        raw = await r.rpop(ALERT_QUEUE_KEY)
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("alert_worker:pop failed", exc_info=True)
        return None


# ── Delivery ───────────────────────────────────────────────────────────


async def deliver_pending_alerts(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Deliver every queued intelligent-webhook alert. Returns processed count."""
    processed = 0
    while True:
        item = await pop_alert()
        if item is None:
            break
        processed += 1
        await _deliver_one(ctx, item)
    if processed:
        logger.info("alert_worker: delivered %s intelligent alert(s)", processed)
    return processed


async def _deliver_one(ctx: ContextTypes.DEFAULT_TYPE, item: dict[str, Any]) -> None:
    user_id = item.get("user_id")
    if not isinstance(user_id, int) or user_id <= 0:
        logger.warning("alert_worker: invalid user_id in alert item")
        return

    event_id = str(item.get("event_id") or "")
    if event_id and not _dedupe(event_id):
        return

    # Per-user rate limit (shared with price alerts: max 1 DM / 5 min).
    if not check_alert_rate_limit(user_id):
        return

    text = _format_alert(item)
    if not text:
        logger.debug(
            "alert_worker: no formatted text for type=%s user=%s", item.get("type"), user_id
        )
        return

    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError as exc:
        logger.debug("alert_worker: send failed to user=%s: %s", user_id, exc)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        logger.warning("alert_worker: send error to user=%s: %s", user_id, exc)


def _dedupe(event_id: str) -> bool:
    """Return True when the event is new (and record it), False on duplicate."""
    global _RECENT_EVENT_IDS
    if event_id in _RECENT_EVENT_IDS:
        return False
    _RECENT_EVENT_IDS.add(event_id)
    if len(_RECENT_EVENT_IDS) > _MAX_RECENT_EVENTS:
        # Drop oldest half to keep the set bounded.
        _RECENT_EVENT_IDS = set(list(_RECENT_EVENT_IDS)[-_MAX_RECENT_EVENTS // 2 :])
    return True


# ── Formatting ─────────────────────────────────────────────────────────


def _fmt_scan_line(addr: str) -> str:
    addr = (addr or "").lower()
    if not addr:
        return ""
    return f"\U0001f50d <b>Scan:</b> /scan <code>{addr}</code>"


def _fmt_chain_short(chain: str) -> str:
    return {
        "ethereum": "ETH",
        "solana": "SOL",
        "bsc": "BSC",
        "polygon": "MATIC",
        "base": "BASE",
    }.get((chain or "").lower(), (chain or "unknown").upper())


def _fmt_money(value: Any) -> str:
    """Format a USD value — ``fmt_number`` already includes the ``$`` sign."""
    try:
        return fmt_number(float(value or 0))
    except (ValueError, TypeError):
        return "$0"


def _format_alert(item: dict[str, Any]) -> str:
    """Build the HTML alert text for a queued event (provider payload)."""
    etype = str(item.get("type") or "unknown")
    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    token_addr = str(payload.get("token_address") or payload.get("address") or "").lower()
    symbol = str(payload.get("token_symbol") or payload.get("symbol") or "").upper() or (
        short_addr(token_addr) if token_addr else "???"
    )
    chain = str(payload.get("chain") or "")

    header = f"{sep()}\n\n"

    if etype == "token.whale_move":
        value = _fmt_money(payload.get("value_decimal"))
        text = (
            f"\U0001f40b <b>Whale Move!</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            f"\U0001f464 <b>From:</b> <code>{payload.get('from_address', '')}</code>\n"
            f"\U0001f3e6 <b>To:</b> <code>{payload.get('to_address', '')}</code>\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.price_change":
        direction = str(payload.get("direction") or "")
        arrow = (
            "\U0001f4c8"
            if direction == "up"
            else "\U0001f4c9"
            if direction == "down"
            else "\U0001f4ca"
        )
        change = str(payload.get("change_pct") or "0")
        price = _fmt_money(payload.get("price"))
        text = (
            f"{arrow} <b>Price Alert: {symbol}</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4ca <b>Change:</b> {change}% ({direction or 'move'})\n"
            f"\U0001f4b5 <b>Price:</b> {price}\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.liquidity_change":
        value = _fmt_money(payload.get("value_decimal"))
        method = str(payload.get("detection_method") or "")
        text = (
            f"\U0001f4a7 <b>Liquidity Change: {symbol}</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            + (f"\U0001f4cb <b>Source:</b> {method}\n" if method else "")
            + f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.deployed":
        name = str(payload.get("token_name") or "")
        deployer = payload.get("deployer_address", "")
        text = (
            f"\U0001f195 <b>New Token Launched</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n"
            + (f"\U0001f3f7\ufe0f <b>Name:</b> {name}\n" if name else "")
            + (f"\U0001f464 <b>Deployer:</b> <code>{deployer}</code>\n" if deployer else "")
            + f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.first_swap":
        value = _fmt_money(payload.get("value_decimal"))
        text = (
            f"\U0001f504 <b>First Swap: {symbol}</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "wallet.activity":
        value = _fmt_money(payload.get("value_decimal"))
        text = (
            f"\U0001f45b <b>Wallet Activity</b>\n{header}"
            f"\U0001f464 <b>From:</b> <code>{payload.get('from_address', '')}</code>\n"
            f"\U0001f3e6 <b>To:</b> <code>{payload.get('to_address', '')}</code>\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    else:
        text = f"\U0001f514 <b>Alert: {symbol}</b>\n{header}\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"

    scan_line = _fmt_scan_line(token_addr)
    text += f"{scan_line}\n" if scan_line else ""
    text += footer_links()
    return text
