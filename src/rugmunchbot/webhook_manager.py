"""
Premium Webhook Manager — comprehensive webhook lifecycle management.

Extends the basic webhook system with:
- Signature verification (HMAC-SHA256)
- Delivery retry with exponential backoff
- Batch delivery
- Event filtering
- Delivery metrics
- Dead letter queue
- Webhook replay
- Rate limiting per subscriber
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WebhookStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    FAILED = "failed"


@dataclass
class WebhookDeliveryRecord:
    """Record of a webhook delivery attempt."""

    webhook_id: str
    event_type: str
    payload: dict[str, Any]
    attempt: int = 0
    max_attempts: int = 3
    status: str = "pending"  # pending, delivered, failed
    response_code: int | None = None
    error: str | None = None
    timestamp: float = 0.0
    latency_ms: float = 0.0


@dataclass
class WebhookMetrics:
    """Metrics for a webhook."""

    total_deliveries: int = 0
    successful_deliveries: int = 0
    failed_deliveries: int = 0
    total_retries: int = 0
    avg_latency_ms: float = 0.0
    last_delivery_at: float = 0.0
    last_failure_at: float = 0.0
    consecutive_failures: int = 0

    @property
    def success_rate(self) -> float:
        if self.total_deliveries == 0:
            return 1.0
        return self.successful_deliveries / self.total_deliveries

    def record(self, success: bool, latency_ms: float) -> None:
        self.total_deliveries += 1
        self.last_delivery_at = time.time()
        if success:
            self.successful_deliveries += 1
            self.consecutive_failures = 0
        else:
            self.failed_deliveries += 1
            self.last_failure_at = time.time()
            self.consecutive_failures += 1
        # Update rolling average
        self.avg_latency_ms = (
            self.avg_latency_ms * (self.total_deliveries - 1) + latency_ms
        ) / self.total_deliveries


def generate_webhook_signature(payload: dict[str, Any], secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hmac.new(
        secret.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_webhook_signature(payload: dict[str, Any], signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature."""
    expected = generate_webhook_signature(payload, secret)
    return hmac.compare_digest(expected, signature)


def should_retry_delivery(status_code: int | None, attempt: int, max_attempts: int) -> bool:
    """Determine if a delivery should be retried."""
    if attempt >= max_attempts:
        return False
    if status_code is None:
        return True
    if status_code >= 500:
        return True
    if status_code == 429:
        return True
    if status_code == 408:
        return True
    return False


def get_retry_delay(attempt: int) -> float:
    """Get exponential backoff delay for retry attempt."""
    return min(300, (2**attempt) * 10)  # 10s, 20s, 40s, 80s, 160s, max 300s


def filter_event_by_subscription(
    event: dict[str, Any],
    subscription_filters: dict[str, Any] | None,
) -> bool:
    """Check if an event matches subscription filters."""
    if not subscription_filters:
        return True

    # Chain filter
    chains = subscription_filters.get("chains", [])
    if chains and event.get("chain") not in chains:
        return False

    # Token address filter
    token_addresses = subscription_filters.get("token_addresses", [])
    if token_addresses:
        event_token = event.get("token_address") or event.get("address")
        if event_token and event_token.lower() not in [t.lower() for t in token_addresses]:
            return False

    # Wallet address filter
    wallet_addresses = subscription_filters.get("wallet_addresses", [])
    if wallet_addresses:
        event_wallet = event.get("wallet") or event.get("address")
        if event_wallet and event_wallet.lower() not in [w.lower() for w in wallet_addresses]:
            return False

    # Min value filter
    min_value = subscription_filters.get("min_value_usd")
    if min_value is not None:
        event_value = event.get("value_usd") or event.get("amount_usd") or 0
        if event_value < min_value:
            return False

    # Price change filter
    price_change_pct = subscription_filters.get("price_change_pct")
    if price_change_pct is not None:
        event_change = event.get("price_change_pct") or 0
        if abs(event_change) < price_change_pct:
            return False

    return True


def batch_events(
    events: list[dict[str, Any]],
    max_batch_size: int = 100,
    max_wait_seconds: float = 5.0,
) -> list[list[dict[str, Any]]]:
    """Batch events for delivery."""
    if not events:
        return []

    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    batch_start_time = time.time()

    for event in events:
        current_batch.append(event)

        if len(current_batch) >= max_batch_size:
            batches.append(current_batch)
            current_batch = []
            batch_start_time = time.time()

        elif max_wait_seconds > 0 and time.time() - batch_start_time >= max_wait_seconds:
            batches.append(current_batch)
            current_batch = []
            batch_start_time = time.time()
        elif max_wait_seconds == 0:
            # Zero wait = flush immediately after each event
            batches.append(current_batch)
            current_batch = []
            batch_start_time = time.time()

    if current_batch:
        batches.append(current_batch)

    return batches


def deduplicate_events(
    events: list[dict[str, Any]],
    key_fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Deduplicate events by key fields."""
    if key_fields is None:
        key_fields = ["event_type", "chain", "token_address", "tx_hash"]

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []

    for event in events:
        key_parts = [str(event.get(f, "")) for f in key_fields]
        key = ":".join(key_parts)
        if key not in seen:
            seen.add(key)
            unique.append(event)

    return unique


class DeadLetterQueue:
    """Queue for failed webhook deliveries."""

    def __init__(self, max_size: int = 1000) -> None:
        self._queue: list[WebhookDeliveryRecord] = []
        self._max_size = max_size

    def add(self, record: WebhookDeliveryRecord) -> None:
        """Add a failed delivery to the queue."""
        if len(self._queue) >= self._max_size:
            self._queue.pop(0)
        self._queue.append(record)

    def get_failed(self, limit: int = 100) -> list[WebhookDeliveryRecord]:
        """Get failed deliveries."""
        return [r for r in self._queue if r.status == "failed"][:limit]

    def mark_replayed(self, record: WebhookDeliveryRecord) -> None:
        """Mark a record as replayed."""
        record.status = "replayed"

    def clear(self) -> None:
        """Clear the queue."""
        self._queue.clear()

    @property
    def size(self) -> int:
        return len(self._queue)
