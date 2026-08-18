"""scam_watch degod — in-process scan_token -> HTTP scanner tool."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_submit_for_scan_uses_http_scanner():
    with patch(
        "rugmunchbot.tools.scanner._scans.scan_token",
        new=AsyncMock(return_value={"risk_flags": ["honeypot"], "safety_score": 0.1}),
    ):
        from rugmunchbot.channels.scam_watch import submit_for_scan

        result = await submit_for_scan("0x123", "ethereum")
        assert result is True


@pytest.mark.asyncio
async def test_submit_for_scan_handles_http_failure():
    with patch(
        "rugmunchbot.tools.scanner._scans.scan_token", side_effect=Exception("network down")
    ):
        from rugmunchbot.channels.scam_watch import submit_for_scan

        result = await submit_for_scan("0x123", "ethereum")
        assert result is False  # fail-open
