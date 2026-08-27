"""Tests for scan memory observability integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from rugmunchbot._vendor.rag_pkg import scan_memory

VECTOR_SIZE = 2560


class TestScanMemoryObservability:
    """Tests that scan memory indexing records observability metrics."""

    @pytest.mark.asyncio
    async def test_successful_index_records_observability(self):
        """Successful scan indexing should call record_rag_index."""
        mock_result = {
            "token_address": "0xabc",
            "chain": "ethereum",
            "name": "Test Token",
            "symbol": "TEST",
            "risk_score": 50,
            "risk_level": "MEDIUM",
            "liquidity": 10000,
            "holders": 100,
            "scanned_at": "2026-08-27T10:00:00Z",
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=AsyncMock(status_code=200))
        mock_client.put = AsyncMock(return_value=AsyncMock(status_code=200))

        with patch(
            "rugmunchbot._vendor.rag_pkg.scan_memory.get_embedding", new_callable=AsyncMock
        ) as mock_embed:
            mock_embed.return_value = [0.0] * VECTOR_SIZE
            with patch("rugmunchbot.observability.record_rag_index") as mock_record:
                result = await scan_memory.index_scan_memory(
                    mock_result,
                    source="scan_v1",
                    client=mock_client,
                )
                assert result["indexed"] is True
                mock_record.assert_called_once_with(success=True, source="scan_v1", latency_ms=0)

    @pytest.mark.asyncio
    async def test_failed_index_records_observability(self):
        """Failed scan indexing should call record_rag_index with success=False."""
        mock_result = {
            "token_address": "0xabc",
            "chain": "ethereum",
            "name": "Test Token",
            "symbol": "TEST",
            "risk_score": 50,
            "scanned_at": "2026-08-27T10:00:00Z",
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=AsyncMock(status_code=200))
        mock_client.put = AsyncMock(return_value=AsyncMock(status_code=400, text="Bad Request"))

        with patch(
            "rugmunchbot._vendor.rag_pkg.scan_memory.get_embedding", new_callable=AsyncMock
        ) as mock_embed:
            mock_embed.return_value = [0.0] * VECTOR_SIZE
            with patch("rugmunchbot.observability.record_rag_index") as mock_record:
                result = await scan_memory.index_scan_memory(
                    mock_result,
                    source="scan_v1",
                    client=mock_client,
                )
                assert result["indexed"] is False
                mock_record.assert_called_once_with(success=False, source="scan_v1", latency_ms=0)

    @pytest.mark.asyncio
    async def test_successful_wallet_index_records_observability(self):
        """Successful wallet indexing should call record_rag_index."""
        mock_result = {
            "address": "0xwallet",
            "chain": "ethereum",
            "trust": {"trust_score": 75, "trust_level": "HIGH"},
            "scanned_at": "2026-08-27T10:00:00Z",
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=AsyncMock(status_code=200))
        mock_client.put = AsyncMock(return_value=AsyncMock(status_code=200))

        with patch(
            "rugmunchbot._vendor.rag_pkg.scan_memory.get_embedding", new_callable=AsyncMock
        ) as mock_embed:
            mock_embed.return_value = [0.0] * VECTOR_SIZE
            with patch("rugmunchbot.observability.record_rag_index") as mock_record:
                result = await scan_memory.index_wallet_memory(
                    mock_result,
                    client=mock_client,
                )
                assert result["indexed"] is True
                mock_record.assert_called_once_with(
                    success=True, source="wallet_scan", latency_ms=0
                )
