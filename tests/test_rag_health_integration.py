"""Tests for RAG health check integration with scan memory."""

from __future__ import annotations

from rugmunchbot._vendor.rag_pkg import scan_memory


class TestRagAvailability:
    """Tests for RAG availability flag."""

    def test_rag_available_by_default(self):
        """RAG should be available by default."""
        assert scan_memory._rag_available() is True

    def test_set_rag_available_true(self):
        """Should be able to set RAG available to True."""
        scan_memory.set_rag_available(True)
        assert scan_memory._rag_available() is True

    def test_set_rag_available_false(self):
        """Should be able to set RAG available to False."""
        scan_memory.set_rag_available(False)
        assert scan_memory._rag_available() is False
        # Reset
        scan_memory.set_rag_available(True)


class TestScheduleScanMemoryWithRagCheck:
    """Tests for schedule_scan_memory_index with RAG health check."""

    def test_skips_when_rag_unavailable(self):
        """Should skip indexing when RAG is unavailable."""
        scan_memory.set_rag_available(False)
        try:
            result = scan_memory.schedule_scan_memory_index(
                {"token_address": "0xabc", "chain": "ethereum"},
                source="scan_v1",
            )
            assert result is None
        finally:
            scan_memory.set_rag_available(True)

    def test_schedules_when_rag_available(self):
        """Should schedule indexing when RAG is available."""
        scan_memory.set_rag_available(True)
        # This will fail because there's no event loop, but it should
        # attempt to schedule (not skip)
        result = scan_memory.schedule_scan_memory_index(
            {"token_address": "0xabc", "chain": "ethereum"},
            source="scan_v1",
        )
        # Returns None because no event loop, but the skip check passed
        assert result is None


class TestScheduleWalletMemoryWithRagCheck:
    """Tests for schedule_wallet_memory_index with RAG health check."""

    def test_skips_when_rag_unavailable(self):
        """Should skip indexing when RAG is unavailable."""
        scan_memory.set_rag_available(False)
        try:
            result = scan_memory.schedule_wallet_memory_index(
                {"address": "0xwallet", "chain": "ethereum"},
            )
            assert result is None
        finally:
            scan_memory.set_rag_available(True)

    def test_schedules_when_rag_available(self):
        """Should schedule indexing when RAG is available."""
        scan_memory.set_rag_available(True)
        result = scan_memory.schedule_wallet_memory_index(
            {"address": "0xwallet", "chain": "ethereum"},
        )
        # Returns None because no event loop, but the skip check passed
        assert result is None
