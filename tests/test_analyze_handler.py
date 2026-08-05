"""
Tests for the V3 AI analysis handler — /analyze + ai_ callback.
External calls (Qwen LLM, scanner) are mocked.
"""

import httpx
import pytest

from handlers.analyze import cmd_analyze, handle_ai_callback  # noqa: E402
from services.scanner import ScanUnavailableError  # noqa: E402
from tests.fakes import FakeCallbackQuery, FakeContext, FakeUpdate, make_message_update  # noqa: E402

SOL_ADDR = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

SCAN_CTX = {
    "address": SOL_ADDR,
    "chain": "solana",
    "risk_score": 72,
    "risk_tier": "high",
    "name": "Test Token",
    "symbol": "TST",
    "warnings": ["HONEYPOT DETECTED"],
}


@pytest.fixture(autouse=True)
def _fresh_db(clean_db):
    yield


async def test_cmd_analyze_no_args_shows_usage():
    update, ctx, msg = make_message_update([])
    await cmd_analyze(update, ctx)
    assert msg.calls[0][0] == "reply"
    assert "AI Analysis" in msg.calls[0][1]


async def test_cmd_analyze_invalid_address():
    update, ctx, msg = make_message_update(["not-an-address"])
    await cmd_analyze(update, ctx)
    assert "Invalid address" in msg.calls[0][1]


async def test_cmd_analyze_success(monkeypatch):
    async def fake_scan(address, chain=None, depth="quick"):
        return dict(SCAN_CTX)

    async def fake_llm(address, scan_ctx):
        return "⚠️ High risk: honeypot indicators detected. Do not buy."

    monkeypatch.setattr("handlers.analyze.scan_token_v2", fake_scan)
    monkeypatch.setattr("handlers.analyze.analyze_token", fake_llm)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_analyze(update, ctx)
    report = msg.calls[1][1]
    assert "RugMunch AI Analysis" in report
    assert "honeypot indicators" in report
    assert "72%" in report  # scanner context bar
    assert "<blockquote>" in report


async def test_cmd_analyze_works_without_scanner_context(monkeypatch):
    async def fail_scan(address, chain=None, depth="quick"):
        raise ScanUnavailableError("scanner down")

    async def fake_llm(address, scan_ctx):
        assert scan_ctx == {}  # degraded gracefully
        return "No scanner context; verify manually."

    monkeypatch.setattr("handlers.analyze.scan_token_v2", fail_scan)
    monkeypatch.setattr("handlers.analyze.analyze_token", fake_llm)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_analyze(update, ctx)
    assert "No scanner context" in msg.calls[1][1]


async def test_cmd_analyze_llm_failure(monkeypatch):
    async def fake_scan(address, chain=None, depth="quick"):
        return dict(SCAN_CTX)

    async def fake_llm(address, scan_ctx):
        raise httpx.ConnectError("llm down")

    monkeypatch.setattr("handlers.analyze.scan_token_v2", fake_scan)
    monkeypatch.setattr("handlers.analyze.analyze_token", fake_llm)
    update, ctx, msg = make_message_update([SOL_ADDR])
    await cmd_analyze(update, ctx)
    assert "AI Analysis Unavailable" in msg.calls[1][1]


async def test_handle_ai_callback_success(monkeypatch):
    async def fake_scan(address, chain=None, depth="quick"):
        return dict(SCAN_CTX)

    async def fake_llm(address, scan_ctx):
        return "Verdict from callback."

    monkeypatch.setattr("handlers.analyze.scan_token_v2", fake_scan)
    monkeypatch.setattr("handlers.analyze.analyze_token", fake_llm)
    q = FakeCallbackQuery(data=f"ai_{SOL_ADDR}_solana")
    update = FakeUpdate(callback_query=q)
    await handle_ai_callback(update, FakeContext())
    assert "Verdict from callback." in q.message.calls[-1][1]


async def test_handle_ai_callback_llm_failure(monkeypatch):
    async def fake_scan(address, chain=None, depth="quick"):
        return dict(SCAN_CTX)

    async def fake_llm(address, scan_ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr("handlers.analyze.scan_token_v2", fake_scan)
    monkeypatch.setattr("handlers.analyze.analyze_token", fake_llm)
    q = FakeCallbackQuery(data=f"ai_{SOL_ADDR}_solana")
    update = FakeUpdate(callback_query=q)
    await handle_ai_callback(update, FakeContext())
    assert "AI Analysis Unavailable" in q.message.calls[-1][1]
