"""Tests for observability enhancements."""

from __future__ import annotations

from rugmunchbot.observability import (
    record_error,
    record_job,
    record_rag_index,
    record_webhook_delivery,
    record_webhook_event,
)


class TestWebhookDeliveryRecording:
    """Tests for record_webhook_delivery."""

    def test_record_successful_delivery(self):
        record_webhook_delivery(success=True, attempts=1, latency_ms=50.0)
        # Should not raise

    def test_record_failed_delivery(self):
        record_webhook_delivery(success=False, attempts=3, latency_ms=100.0)
        # Should not raise

    def test_record_delivery_with_high_attempts(self):
        record_webhook_delivery(success=False, attempts=10, latency_ms=500.0)
        # Should not raise, attempts capped at 5


class TestRagIndexRecording:
    """Tests for record_rag_index."""

    def test_record_successful_index(self):
        record_rag_index(success=True, source="scan_v1", latency_ms=25.0)
        # Should not raise

    def test_record_failed_index(self):
        record_rag_index(success=False, source="news", latency_ms=100.0)
        # Should not raise

    def test_record_index_various_sources(self):
        for source in ["scan_v1", "scan_v2", "sentinel", "news", "deepscan"]:
            record_rag_index(success=True, source=source, latency_ms=50.0)
        # Should not raise


class TestExistingObservability:
    """Tests for existing observability functions."""

    def test_record_webhook_event(self):
        record_webhook_event("price_change")
        # Should not raise

    def test_record_job(self):
        record_job("test_job")
        # Should not raise

    def test_record_error(self):
        record_error("test_error", "Something went wrong")
        # Should not raise
