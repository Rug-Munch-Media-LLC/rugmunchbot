# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/webhooks/repository.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Webhook subscription repository — async Redis storage.

Storage layout:
  Hash key: "rmi:webhooks"
  Field:    subscription ID (e.g. "whk:1712000000")
  Value:    JSON-encoded WebhookSubscription

This is a thin async wrapper following the AlertRepository pattern.
No business logic. Pure data access.
"""

from __future__ import annotations

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot._vendor.webhook_models import WebhookSubscription

log = get_logger(__name__)

HASH_KEY = "rmi:webhooks"


class WebhookRepository:
    """Async Redis-backed webhook subscription storage."""

    def __init__(self) -> None:
        self._key = HASH_KEY

    async def list_all(self) -> list[WebhookSubscription]:
        """Return every subscription."""
        r = get_redis_async()
        if r is None:
            log.warning("webhook_repo_redis_unavailable")
            return []
        raw: dict[bytes | str, bytes | str] = await r.hgetall(self._key) or {}
        out: list[WebhookSubscription] = []
        for value in raw.values():
            try:
                out.append(WebhookSubscription.model_validate_json(value))
            except (ValueError, TypeError, KeyError, OSError) as exc:
                log.warning("webhook_repo_corrupt_record", error=str(exc))
        return out

    async def get(self, sub_id: str) -> WebhookSubscription | None:
        """Get a single subscription by ID."""
        r = get_redis_async()
        if r is None:
            log.warning("webhook_repo_redis_unavailable")
            return None
        raw: bytes | str | None = await r.hget(self._key, sub_id)
        if raw is None:
            return None
        try:
            return WebhookSubscription.model_validate_json(raw)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            log.warning("webhook_repo_corrupt_record", sub_id=sub_id, error=str(exc))
            return None

    async def create(self, sub: WebhookSubscription) -> None:
        """Persist a new subscription."""
        r = get_redis_async()
        if r is None:
            log.warning("webhook_repo_redis_unavailable")
            return
        await r.hset(self._key, sub.id, sub.model_dump_json())
        log.info("webhook_subscription_created", sub_id=sub.id, types=[t.value for t in sub.event_types])

    async def delete(self, sub_id: str) -> bool:
        """Remove a subscription. Returns True if it existed."""
        r = get_redis_async()
        if r is None:
            log.warning("webhook_repo_redis_unavailable")
            return False
        removed: int = await r.hdel(self._key, sub_id)
        if removed:
            log.info("webhook_subscription_deleted", sub_id=sub_id)
        return removed > 0

    async def update(self, sub: WebhookSubscription) -> None:
        """Update an existing subscription."""
        await self.create(sub)

    async def list_by_event_type(self, event_type: str) -> list[WebhookSubscription]:
        """Return active subscriptions matching a specific event type."""
        all_subs = await self.list_all()
        return [s for s in all_subs if s.active and event_type in [t.value for t in s.event_types]]
