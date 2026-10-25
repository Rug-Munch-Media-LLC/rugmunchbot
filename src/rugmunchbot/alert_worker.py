#!/usr/bin/env python3
"""
RugMunchBot — intelligent webhook alert delivery worker.

Background job (registered in bot.py, every 30s) that drains the Redis LIST
``bot:alerts:queue`` written by the backend webhook bridge
(``app/domains/scanner/webhooks/telegram_bridge.py``) and DMs each event to its
subscriber. Items are JSON::

    {"type": "token.whale_move" | "token.price_change" | ...,
     "user_id": int, "event_id": str, "payload": {...}, "created_at": str}

Delivery mirrors ``deliver_pending_broadcasts``: pop FIFO → premium watch gate
(``token.monitor_change`` only) → format → send, paced at ``SEND_DELAY``. A
per-user rate limit (1 alert / 5 min, shared with price alerts) and a bounded
in-memory dedupe set apply; Redis/DB trouble skips the tick rather than dying.

A failed send is NEVER silently dropped: the item is re-queued with a
``retry_count`` (bounded by ``MAX_ALERT_RETRIES``) and parked in
``bot:alerts:dead`` once the budget is spent. Quiet-hours holds re-queue
without spending a retry — they are not failures.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot.formatters.alert_items import format_alert as _format_alert
from rugmunchbot.premium.watch_gate import screen
from rugmunchbot.states import check_alert_rate_limit
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

ALERT_QUEUE_KEY = "bot:alerts:queue"
ALERT_DEAD_LETTER_KEY = "bot:alerts:dead"  # alerts that exhausted their retry budget
SEND_DELAY = 0.05  # seconds between sends (Telegram rate-limit politeness)
MAX_ALERT_RETRIES = 3  # re-queue budget per alert; beyond → dead-letter list
_GATE_HOLD = "_gate_hold"  # quiet-hours deferral marker: re-queue, no retry spent

# Bounded dedupe: the backend may dispatch one event to several overlapping
# subscriptions, so repeats within the most-recent window are dropped.
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
        payload = raw[0] if isinstance(raw, list) else raw
        data = json.loads(payload)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("alert_worker:pop failed", exc_info=True)
        return None


# ── Delivery ───────────────────────────────────────────────────────────


async def deliver_pending_alerts(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Deliver every queued intelligent-webhook alert. Returns processed count."""
    processed = 0
    # A re-queued item must wait for the NEXT tick (the job runs every 30s):
    # retrying inside one burst would hammer Telegram. Identity-track them.
    requeued_this_tick: set[str] = set()
    while True:
        item = await pop_alert()
        if item is None:
            break
        identity = _item_identity(item)
        if identity in requeued_this_tick:
            # Failed earlier this tick and re-queued at the tail: put it back
            # and leave it for the next tick.
            await _push_alert(item)
            break
        processed += 1
        if not await _deliver_one(ctx, item):
            await _requeue_or_deadletter(item)
            requeued_this_tick.add(identity)
        # Pace sends like the broadcast worker (Telegram rate-limit politeness).
        await asyncio.sleep(SEND_DELAY)
    if processed:
        logger.info("alert_worker: delivered %s intelligent alert(s)", processed)
    return processed


def _valid_user(item: dict[str, Any]) -> bool:
    user_id = item.get("user_id")
    if isinstance(user_id, int) and user_id > 0:
        return True
    logger.warning("alert_worker: invalid user_id in alert item")
    return False


def _admitted(item: dict[str, Any]) -> bool:
    """True when this item may be sent now: dedupe → watch gate → rate limit."""
    event_id = str(item.get("event_id") or "")
    if event_id and not _dedupe(event_id):
        return False
    verdict = screen(item, forget=_RECENT_EVENT_IDS.discard)  # premium watch gate
    if verdict == "hold":
        item[_GATE_HOLD] = True  # deferred, not failed: keep the retry budget
        return False
    if verdict == "drop":
        return False
    # Retried items skip the per-user limit: the failed attempt took the slot.
    return bool(item.get("retry_count")) or check_alert_rate_limit(item["user_id"])


def _text_for(item: dict[str, Any]) -> str | None:
    """Formatted HTML, or None when this item can never be rendered."""
    user_id = item["user_id"]
    try:
        text = _format_alert(item)
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning(
            "alert_worker: cannot format alert type=%s user=%s: %s", item.get("type"), user_id, exc
        )
        return None
    if not text:
        logger.debug("alert_worker: blank text type=%s user=%s", item.get("type"), user_id)
        return None
    return text


def _record_delivery(item: dict[str, Any]) -> None:
    try:  # metrics: count the delivered event (best-effort, never raises)
        from rugmunchbot.observability import record_webhook_event

        record_webhook_event(str(item.get("type") or "unknown"))
    except Exception:
        logger.debug("observability record_webhook_event failed", exc_info=True)


async def _send_alert(ctx: ContextTypes.DEFAULT_TYPE, item: dict[str, Any], text: str) -> bool:
    """Send one rendered alert; False when the send failed and must be retried."""
    user_id = item["user_id"]
    from rugmunchbot.content_style import prepare_for_send

    try:
        for part in prepare_for_send(text):
            await ctx.bot.send_message(
                chat_id=user_id,
                text=part,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception as exc:  # TelegramError and friends — never drop a failed send
        item["last_error"] = str(exc)
        logger.warning(
            "alert_worker: send failed to user=%s type=%s: %s", user_id, item.get("type"), exc
        )
        return False
    _record_delivery(item)
    return True


async def _deliver_one(ctx: ContextTypes.DEFAULT_TYPE, item: dict[str, Any]) -> bool:
    """Deliver one alert.

    True = no retry needed (sent, or skipped: invalid, duplicate, dropped,
    rate-limited, unformattable). False = re-queue (failed send, or held).
    """
    if not _valid_user(item):
        return True
    if not _admitted(item):
        return _GATE_HOLD not in item
    text = _text_for(item)
    if text is None:
        return True
    return await _send_alert(ctx, item, text)


async def _requeue_or_deadletter(item: dict[str, Any]) -> None:
    """Re-queue a failed alert (bounded retries) or dead-letter it.

    Never drops silently: re-queued for the next tick, or parked in
    ``bot:alerts:dead`` once ``MAX_ALERT_RETRIES`` is exhausted.
    """
    if item.pop(_GATE_HOLD, False):
        # Quiet hours held it; it is not a failure, so keep the retry budget.
        await _push_alert(item)
        return
    retries = int(item.get("retry_count") or 0) + 1
    item["retry_count"] = retries
    reason = str(item.get("last_error") or "unknown")
    summary = (
        f"type={item.get('type')} user={item.get('user_id')} event={item.get('event_id') or '?'}"
    )
    r = get_redis_async()
    if r is None:
        logger.warning(
            "alert_worker: redis unavailable — alert not re-queued (%s): %s", summary, reason
        )
        return
    try:
        if retries >= MAX_ALERT_RETRIES:
            await r.lpush(ALERT_DEAD_LETTER_KEY, json.dumps(item))
            logger.warning(
                "alert_worker: alert dead-lettered after %s attempts (%s): %s",
                retries,
                summary,
                reason,
            )
            try:
                from rugmunchbot.observability import record_error

                record_error(kind="alert_deadletter", message=f"{summary}: {reason}")
            except Exception:
                logger.debug("observability record_error failed", exc_info=True)
        else:
            await r.lpush(ALERT_QUEUE_KEY, json.dumps(item))
            logger.warning(
                "alert_worker: alert re-queued for retry %s/%s (%s): %s",
                retries,
                MAX_ALERT_RETRIES,
                summary,
                reason,
            )
    except Exception:
        logger.warning("alert_worker: re-queue failed (%s)", summary, exc_info=True)


async def _push_alert(item: dict[str, Any], key: str = ALERT_QUEUE_KEY) -> None:
    """Push an alert item back onto a Redis LIST (re-queue / dead-letter / tick halt)."""
    r = get_redis_async()
    if r is None:
        return
    try:
        await r.lpush(key, json.dumps(item))
    except Exception:
        logger.warning("alert_worker: push back failed for key=%s", key, exc_info=True)


def _item_identity(item: dict[str, Any]) -> str:
    """Stable identity: event_id when present, else normalized JSON.

    ``retry_count``/``last_error`` are excluded so a retried item keeps it.
    """
    event_id = str(item.get("event_id") or "")
    if event_id:
        return f"evt:{event_id}"
    stable = {k: v for k, v in item.items() if k not in ("retry_count", "last_error")}
    return json.dumps(stable, sort_keys=True, default=str)


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
