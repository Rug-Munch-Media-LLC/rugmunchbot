"""Tests for Premium Webhook Manager."""

from __future__ import annotations

from rugmunchbot.webhook_manager import (
    DeadLetterQueue,
    WebhookDeliveryRecord,
    WebhookMetrics,
    WebhookStatus,
    batch_events,
    deduplicate_events,
    filter_event_by_subscription,
    generate_webhook_signature,
    get_retry_delay,
    should_retry_delivery,
    verify_webhook_signature,
)


class TestWebhookStatus:
    """Tests for WebhookStatus enum."""

    def test_active(self):
        assert WebhookStatus.ACTIVE.value == "active"

    def test_failed(self):
        assert WebhookStatus.FAILED.value == "failed"


class TestWebhookMetrics:
    """Tests for WebhookMetrics."""

    def test_initial_state(self):
        metrics = WebhookMetrics()
        assert metrics.total_deliveries == 0
        assert metrics.success_rate == 1.0

    def test_record_success(self):
        metrics = WebhookMetrics()
        metrics.record(True, 50.0)
        assert metrics.total_deliveries == 1
        assert metrics.successful_deliveries == 1
        assert metrics.success_rate == 1.0

    def test_record_failure(self):
        metrics = WebhookMetrics()
        metrics.record(False, 50.0)
        assert metrics.total_deliveries == 1
        assert metrics.failed_deliveries == 1
        assert metrics.success_rate == 0.0

    def test_consecutive_failures(self):
        metrics = WebhookMetrics()
        metrics.record(False, 50.0)
        metrics.record(False, 50.0)
        assert metrics.consecutive_failures == 2
        metrics.record(True, 50.0)
        assert metrics.consecutive_failures == 0

    def test_avg_latency(self):
        metrics = WebhookMetrics()
        metrics.record(True, 100.0)
        metrics.record(True, 200.0)
        assert metrics.avg_latency_ms == 150.0


class TestSignatureVerification:
    """Tests for HMAC-SHA256 signature verification."""

    def test_generate_signature(self):
        payload = {"event": "test", "data": "value"}
        secret = "my_secret"
        sig = generate_webhook_signature(payload, secret)
        assert len(sig) == 64

    def test_verify_valid_signature(self):
        payload = {"event": "test", "data": "value"}
        secret = "my_secret"
        sig = generate_webhook_signature(payload, secret)
        assert verify_webhook_signature(payload, sig, secret) is True

    def test_verify_invalid_signature(self):
        payload = {"event": "test", "data": "value"}
        secret = "my_secret"
        assert verify_webhook_signature(payload, "invalid_sig", secret) is False

    def test_different_secrets_produce_different_signatures(self):
        payload = {"event": "test"}
        sig1 = generate_webhook_signature(payload, "secret1")
        sig2 = generate_webhook_signature(payload, "secret2")
        assert sig1 != sig2


class TestRetryLogic:
    """Tests for delivery retry logic."""

    def test_should_retry_on_500(self):
        assert should_retry_delivery(500, 1, 3) is True

    def test_should_retry_on_429(self):
        assert should_retry_delivery(429, 1, 3) is True

    def test_should_retry_on_408(self):
        assert should_retry_delivery(408, 1, 3) is True

    def test_should_not_retry_on_400(self):
        assert should_retry_delivery(400, 1, 3) is False

    def test_should_not_retry_on_200(self):
        assert should_retry_delivery(200, 1, 3) is False

    def test_should_not_retry_when_max_attempts_reached(self):
        assert should_retry_delivery(500, 3, 3) is False

    def test_should_retry_on_none_status(self):
        assert should_retry_delivery(None, 1, 3) is True

    def test_retry_delay_exponential(self):
        assert get_retry_delay(0) == 10
        assert get_retry_delay(1) == 20
        assert get_retry_delay(2) == 40
        assert get_retry_delay(3) == 80

    def test_retry_delay_capped(self):
        assert get_retry_delay(10) == 300


class TestEventFiltering:
    """Tests for event filtering."""

    def test_no_filters_passes_all(self):
        event = {"chain": "ethereum", "token_address": "0xabc"}
        assert filter_event_by_subscription(event, None) is True

    def test_chain_filter_match(self):
        event = {"chain": "ethereum"}
        filters = {"chains": ["ethereum", "bsc"]}
        assert filter_event_by_subscription(event, filters) is True

    def test_chain_filter_no_match(self):
        event = {"chain": "solana"}
        filters = {"chains": ["ethereum", "bsc"]}
        assert filter_event_by_subscription(event, filters) is False

    def test_token_filter_match(self):
        event = {"token_address": "0xabc"}
        filters = {"token_addresses": ["0xabc", "0xdef"]}
        assert filter_event_by_subscription(event, filters) is True

    def test_token_filter_no_match(self):
        event = {"token_address": "0xghi"}
        filters = {"token_addresses": ["0xabc", "0xdef"]}
        assert filter_event_by_subscription(event, filters) is False

    def test_min_value_filter_pass(self):
        event = {"value_usd": 10000}
        filters = {"min_value_usd": 5000}
        assert filter_event_by_subscription(event, filters) is True

    def test_min_value_filter_fail(self):
        event = {"value_usd": 1000}
        filters = {"min_value_usd": 5000}
        assert filter_event_by_subscription(event, filters) is False

    def test_price_change_filter_pass(self):
        event = {"price_change_pct": 10}
        filters = {"price_change_pct": 5}
        assert filter_event_by_subscription(event, filters) is True

    def test_price_change_filter_fail(self):
        event = {"price_change_pct": 2}
        filters = {"price_change_pct": 5}
        assert filter_event_by_subscription(event, filters) is False


class TestBatchEvents:
    """Tests for event batching."""

    def test_empty_list(self):
        result = batch_events([])
        assert result == []

    def test_single_event(self):
        events = [{"id": "1"}]
        result = batch_events(events)
        assert len(result) == 1
        assert len(result[0]) == 1

    def test_batch_by_size(self):
        events = [{"id": str(i)} for i in range(150)]
        result = batch_events(events, max_batch_size=100)
        assert len(result) == 2
        assert len(result[0]) == 100
        assert len(result[1]) == 50

    def test_batch_by_time(self):
        events = [{"id": str(i)} for i in range(5)]
        result = batch_events(events, max_batch_size=100, max_wait_seconds=0)
        # With max_wait_seconds=0, each event triggers its own batch
        assert len(result) == 5


class TestDeduplication:
    """Tests for event deduplication."""

    def test_deduplicates(self):
        events = [
            {"event_type": "swap", "chain": "ethereum", "tx_hash": "0x1"},
            {"event_type": "swap", "chain": "ethereum", "tx_hash": "0x1"},
            {"event_type": "swap", "chain": "ethereum", "tx_hash": "0x2"},
        ]
        result = deduplicate_events(events)
        assert len(result) == 2

    def test_keeps_unique(self):
        events = [
            {"event_type": "swap", "chain": "ethereum", "tx_hash": "0x1"},
            {"event_type": "transfer", "chain": "ethereum", "tx_hash": "0x2"},
        ]
        result = deduplicate_events(events)
        assert len(result) == 2


class TestDeadLetterQueue:
    """Tests for DeadLetterQueue."""

    def test_add_and_get(self):
        queue = DeadLetterQueue()
        record = WebhookDeliveryRecord(
            webhook_id="wh_1",
            event_type="test",
            payload={},
            status="failed",
        )
        queue.add(record)
        failed = queue.get_failed()
        assert len(failed) == 1

    def test_max_size(self):
        queue = DeadLetterQueue(max_size=2)
        for i in range(3):
            record = WebhookDeliveryRecord(
                webhook_id=f"wh_{i}",
                event_type="test",
                payload={},
                status="failed",
            )
            queue.add(record)
        assert queue.size == 2

    def test_mark_replayed(self):
        queue = DeadLetterQueue()
        record = WebhookDeliveryRecord(
            webhook_id="wh_1",
            event_type="test",
            payload={},
            status="failed",
        )
        queue.add(record)
        queue.mark_replayed(record)
        assert record.status == "replayed"

    def test_clear(self):
        queue = DeadLetterQueue()
        record = WebhookDeliveryRecord(
            webhook_id="wh_1",
            event_type="test",
            payload={},
            status="failed",
        )
        queue.add(record)
        queue.clear()
        assert queue.size == 0
