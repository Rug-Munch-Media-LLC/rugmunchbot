"""Tests for support.system_alerts — fail-open alerting to @rmiadminbot."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from rugmunchbot.support import system_alerts
from rugmunchbot.support.system_alerts import send_system_alert


@pytest.fixture(autouse=True)
def _admin_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_alerts, "ADMIN_CHAT_ID", "12345")


@pytest.mark.asyncio
async def test_sends_formatted_message() -> None:
    bot = AsyncMock()
    await send_system_alert(bot, "critical", "indexer", "stall detected", lag=120)
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == "12345"
    assert "🔴" in kwargs["text"]
    assert "[CRITICAL] indexer" in kwargs["text"]
    assert "stall detected" in kwargs["text"]
    assert "lag: 120" in kwargs["text"]


@pytest.mark.asyncio
async def test_no_chat_id_logs_and_skips(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(system_alerts, "ADMIN_CHAT_ID", "")
    bot = AsyncMock()
    with caplog.at_level(logging.WARNING):
        await send_system_alert(bot, "high", "scanner", "boom")
    bot.send_message.assert_not_awaited()
    assert "system_alert_no_admin_chat_id" in caplog.text


@pytest.mark.asyncio
async def test_send_failure_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("telegram down")
    with caplog.at_level(logging.WARNING):
        await send_system_alert(bot, "medium", "infra", "disk 90%")
    assert "system_alert_send_failed" in caplog.text


@pytest.mark.asyncio
async def test_unknown_severity_gets_default_emoji() -> None:
    bot = AsyncMock()
    await send_system_alert(bot, "weird", "mod", "msg")
    assert "⚪" in bot.send_message.await_args.kwargs["text"]
