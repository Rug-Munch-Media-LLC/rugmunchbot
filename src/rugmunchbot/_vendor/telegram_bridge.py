# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/webhooks/telegram_bridge.py (Phase 2a — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Telegram alert bridge — routes webhook events to the RugMunchBot alert queue.

The webhook engine delivers events to subscribers over HTTP (external URLs).
This module adds an *internal* delivery target: a subscription whose URL is
``internal://telegram/{user_id}``. Instead of an HTTP POST, matching events
are enqueued as JSON onto the Redis LIST ``bot:alerts:queue`` — the same
queue the bot's ``deliver_pending_alerts`` job (registered in bot.py, runs
every 30s, mirrors the proven ``deliver_pending_broadcasts`` pattern) pops
and formats into HTML push alerts.

Architecture::

    provider poll → WebhookEngine._dispatch
        ├── external subscription → WebhookDeliveryService.deliver()  (HTTP POST)
        └── internal://telegram/{user_id} subscription
                └── route_internal_telegram() → Redis LIST bot:alerts:queue
                        └── bot deliver_pending_alerts job (30s)
                                └── formatted HTML alert → send_message(user_id)

The bot side is scoped to delivery only: formatting lives in
app/domains/telegram/rugmunchbot/alert_worker.py. This module owns the
backend half (queue writes + internal subscription CRUD) so the backend API
can subscribe a chat to real events (see POST /api/v1/scanner/webhooks/telegram).

Fails open: when Redis is unavailable the event is dropped and a warning is
logged — never a hard failure for the engine poll loop.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot._vendor.webhook_models import (
    WebhookEvent,
    WebhookEventType,
    WebhookFilters,
    WebhookSubscription,
)
from rugmunchbot._vendor.webhook_repository import WebhookRepository

log = get_logger(__name__)

# URL scheme for bot-internal subscriptions. ``WebhookDeliveryService`` only
# ever sees external http(s) URLs — internal ones are intercepted in the
# engine's ``_dispatch`` before HTTP delivery is attempted.
INTERNAL_TELEGRAM_PREFIX = "internal://telegram/"

# Redis LIST key shared with the bot's alert delivery worker. JSON items:
#   {"type": str, "user_id": int, "event_id": str,
#    "payload": dict, "created_at": str}
TELEGRAM_ALERT_QUEUE_KEY = "bot:alerts:queue"


# ══════════════════════════════════════════════
# Internal subscription helpers
# ══════════════════════════════════════════════


def is_telegram_subscription(sub: WebhookSubscription) -> bool:
    """True when the subscription targets the bot's internal alert queue."""
    return sub.url.startswith(INTERNAL_TELEGRAM_PREFIX)


def telegram_user_id(sub: WebhookSubscription) -> int | None:
    """Extract the target Telegram user_id from an internal subscription."""
    if not is_telegram_subscription(sub):
        return None
    raw = sub.url[len(INTERNAL_TELEGRAM_PREFIX) :].strip()
    return int(raw) if raw.isdigit() else None


def _build_internal_subscription(
    user_id: int,
    event_types: list[WebhookEventType],
    *,
    token_addresses: list[str] | None = None,
    chains: list[str] | None = None,
    price_change_pct: float | None = None,
    min_value_usd: float | None = None,
) -> WebhookSubscription:
    """Construct an internal telegram subscription.

    ``model_construct`` is used (instead of the normal constructor) so the
    ``url`` http(s) validator is bypassed for the trusted internal scheme.
    The public create endpoint still rejects non-http URLs.
    """
    filters = WebhookFilters(
        chains=[c.lower() for c in chains] if chains else [],
        token_addresses=[a.lower() for a in token_addresses] if token_addresses else [],
        min_value_usd=min_value_usd,
        price_change_pct=price_change_pct,
    )
    sub = WebhookSubscription.model_construct(
        id=f"whk:tg:{int(user_id)}:{int(datetime.now(UTC).timestamp())}:{uuid.uuid4().hex[:6]}",
        url=f"{INTERNAL_TELEGRAM_PREFIX}{int(user_id)}",
        event_types=event_types,
        filters=filters,
        active=True,
        created_at=datetime.now(UTC),
        owner_id=str(user_id),
        last_triggered_at=None,
    )
    return sub


async def subscribe_telegram_user(
    user_id: int,
    event_types: list[WebhookEventType],
    *,
    token_addresses: list[str] | None = None,
    chains: list[str] | None = None,
    price_change_pct: float | None = None,
    min_value_usd: float | None = None,
    repo: WebhookRepository | None = None,
) -> WebhookSubscription | None:
    """Subscribe a bot user's chat to real webhook events (whale/price/…).

    Creates one internal subscription per (user, event_type) — the engine
    maps by token address so per-token filters are merged by creating a
    subscription per token address set. Returns the created subscription or
    None on failure (Redis unavailable).
    """
    if not event_types:
        return None
    sub = _build_internal_subscription(
        user_id,
        event_types,
        token_addresses=token_addresses,
        chains=chains,
        price_change_pct=price_change_pct,
        min_value_usd=min_value_usd,
    )
    try:
        repo = repo or WebhookRepository()
        await repo.create(sub)
    except Exception as exc:
        log.warning("telegram_bridge_subscribe_failed user=%s error=%s", user_id, exc)
        return None
    log.info(
        "telegram_alert_subscribed",
        user_id=user_id,
        sub_id=sub.id,
        types=[t.value for t in event_types],
        tokens=len(sub.filters.token_addresses) if sub.filters else 0,
    )
    return sub


async def unsubscribe_telegram_user(
    user_id: int,
    event_types: list[WebhookEventType] | None = None,
    token_addresses: list[str] | None = None,
    repo: WebhookRepository | None = None,
) -> int:
    """Remove internal telegram subscriptions for a user.

    ``event_types`` / ``token_addresses`` narrow the removal. Returns the
    number of subscriptions deleted.
    """
    try:
        repo = repo or WebhookRepository()
        all_subs = await repo.list_all()
    except Exception as exc:
        log.warning("telegram_bridge_unsubscribe_failed user=%s error=%s", user_id, exc)
        return 0

    removed = 0
    wanted_types = {t.value for t in event_types} if event_types else None
    wanted_tokens = {a.lower() for a in token_addresses} if token_addresses else None

    for sub in all_subs:
        if not is_telegram_subscription(sub):
            continue
        if telegram_user_id(sub) != int(user_id):
            continue
        if wanted_types is not None and not (set(sub.event_types) & wanted_types):
            continue
        if wanted_tokens is not None:
            sub_tokens = {a.lower() for a in (sub.filters.token_addresses if sub.filters else [])}
            if not (sub_tokens & wanted_tokens):
                continue
        try:
            if await repo.delete(sub.id):
                removed += 1
        except Exception as exc:
            log.warning("telegram_bridge_unsubscribe_del_failed sub=%s error=%s", sub.id, exc)

    if removed:
        log.info("telegram_alert_unsubscribed", user_id=user_id, removed=removed)
    return removed


# ══════════════════════════════════════════════
# Queue writes (backend → bot)
# ══════════════════════════════════════════════


async def enqueue_telegram_alert(
    user_id: int,
    event_type: str,
    payload: dict[str, Any],
    event_id: str,
) -> bool:
    """Push one alert onto the bot's alert queue (FIFO via LPUSH/RPOP).

    Returns True when the item was queued. Fails open (False + warning)
    when Redis is unavailable — never raises.
    """
    r = get_redis_async()
    if r is None:
        log.warning("telegram_bridge_redis_unavailable event=%s user=%s", event_type, user_id)
        return False
    item = {
        "type": event_type,
        "user_id": int(user_id),
        "event_id": event_id,
        "payload": payload,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        await r.lpush(TELEGRAM_ALERT_QUEUE_KEY, json.dumps(item))
        return True
    except Exception as exc:
        log.warning("telegram_bridge_enqueue_failed event=%s user=%s error=%s", event_type, user_id, exc)
        return False


async def route_internal_telegram(event: WebhookEvent, sub: WebhookSubscription) -> bool:
    """Route an event to the bot's alert queue when the sub is internal.

    Called from ``WebhookEngine._dispatch``. Returns True when the event was
    handled internally (caller must NOT perform HTTP delivery), False when
    the subscription is external (caller proceeds with HTTP delivery).
    """
    user_id = telegram_user_id(sub)
    if user_id is None:
        return False

    event_type = event.event_type if isinstance(event.event_type, str) else event.event_type.value
    payload = dict(event.payload or {})
    payload.setdefault("chain", event.chain or "unknown")

    return await enqueue_telegram_alert(
        user_id=user_id,
        event_type=event_type,
        payload=payload,
        event_id=event.id,
    )


__all__ = [
    "INTERNAL_TELEGRAM_PREFIX",
    "TELEGRAM_ALERT_QUEUE_KEY",
    "enqueue_telegram_alert",
    "is_telegram_subscription",
    "route_internal_telegram",
    "subscribe_telegram_user",
    "telegram_user_id",
    "unsubscribe_telegram_user",
]
