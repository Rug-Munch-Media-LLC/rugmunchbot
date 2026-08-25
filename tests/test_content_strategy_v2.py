"""CONTENT-STRATEGY-CANONICAL v2 quality-push tests.

Covers the four gaps closed in this session:

1. BETA_FREE_DEEPSCAN flag — paywall branch skipped + beta banner on reports.
2. W30 mirror-failure alerting — streak dedup, recovery reset, fail-open.
3. W8 alerts→RAG — fresh alerts handed to the scan-memory indexer.
4. Per-channel style guides — fragments exist, accessor fails soft, rewrite
   prompt carries the channel contract.
"""

from __future__ import annotations

# ── Fix 1: BETA_FREE_DEEPSCAN ───────────────────────────────────────────
import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import rugmunchbot.handlers._scan_handlers._deepscan as _ds_module


@pytest.fixture(autouse=True)
def _hermetic_beta_flag(monkeypatch):
    """Reload the deepscan module with a KNOWN env state per test.

    BETA_FREE_DEEPSCAN is read at import time, so tests that flip it must
    reload the module — and the module must be restored afterwards or the
    leak poisons every later test in the session (full-suite ordering).
    """
    monkeypatch.delenv("BETA_FREE_DEEPSCAN", raising=False)
    importlib.reload(_ds_module)
    yield
    monkeypatch.delenv("BETA_FREE_DEEPSCAN", raising=False)
    importlib.reload(_ds_module)


def test_beta_flag_default_off():
    assert _ds_module.BETA_FREE_DEEPSCAN is False
    assert _ds_module._beta_banner_line() == ""


def test_beta_banner_line_when_on(monkeypatch):
    monkeypatch.setenv("BETA_FREE_DEEPSCAN", "1")
    reloaded = importlib.reload(_ds_module)
    assert reloaded.BETA_FREE_DEEPSCAN is True
    line = reloaded._beta_banner_line()
    assert "Free during beta" in line
    assert line.startswith("\n")


@pytest.mark.asyncio
async def test_deepscan_paywall_skipped_when_beta_free(monkeypatch):
    """Rate-limited non-owner gets a scan, not a paywall, when beta-free."""
    monkeypatch.setenv("BETA_FREE_DEEPSCAN", "1")
    ds = importlib.reload(_ds_module)
    assert ds.BETA_FREE_DEEPSCAN is True

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=999, username="u", first_name="U"),
        message=SimpleNamespace(args=["0xabc"], reply_text=None, edit_text=None),
    )
    # reply_text records the FIRST call; if it's the paywall text the
    # contract is broken.
    calls: list = []

    async def _reply(text, **_kw):
        calls.append(text)

    update.message.reply_text = _reply  # type: ignore[method-assign]

    with (
        patch.object(ds.db, "get_or_create_user"),
        patch.object(ds.db, "check_rate_limit", return_value=(False, 5, 5)),
        patch.object(ds.is_owner, "__wrapped__", create=True),
        patch.object(ds, "is_owner", lambda _uid: False),
    ):
        await ds.cmd_deepscan(update, SimpleNamespace(args=[]))
    assert not any("upgrade" in str(c).lower() for c in calls), (
        "paywall branch must be skipped while BETA_FREE_DEEPSCAN=1"
    )


# ── Fix 2: W30 mirror-failure alerting ──────────────────────────────────


@pytest.mark.asyncio
async def test_mirror_alert_fires_once_per_streak():
    from rugmunchbot.channels.scheduler import _posts_rag

    _posts_rag._mirror_fail_streak = 0
    sent: list[str] = []

    class _Bot:
        async def send_message(self, chat_id, text, **_kw):
            sent.append(text)

    with patch("rugmunchbot.config._resolve_channel_id", return_value="-100ops"):
        await _posts_rag._alert_mirror_failure(_Bot(), "boom-1")
        await _posts_rag._alert_mirror_failure(_Bot(), "boom-2")
        await _posts_rag._alert_mirror_failure(_Bot(), "boom-3")

    assert len(sent) == 1, "one alert per failure streak, not per tick"
    assert "Feed mirror DOWN" in sent[0]
    _posts_rag._mirror_fail_streak = 0


@pytest.mark.asyncio
async def test_mirror_alert_resets_and_refires_after_recovery():
    from rugmunchbot.channels.scheduler import _posts_rag

    _posts_rag._mirror_fail_streak = 0
    sent: list[str] = []

    class _Bot:
        async def send_message(self, chat_id, text, **_kw):
            sent.append(text)

    with patch("rugmunchbot.config._resolve_channel_id", return_value="-100ops"):
        await _posts_rag._alert_mirror_failure(_Bot(), "a")
        _posts_rag._note_mirror_success()  # recovered
        await _posts_rag._alert_mirror_failure(_Bot(), "b")  # new streak → new alert

    assert len(sent) == 2
    _posts_rag._mirror_fail_streak = 0


def test_note_mirror_success_is_quiet_when_no_streak():
    from rugmunchbot.channels.scheduler import _posts_rag

    _posts_rag._mirror_fail_streak = 0
    _posts_rag._note_mirror_success()  # must not raise
    assert _posts_rag._mirror_fail_streak == 0


# ── Fix 3: W8 alerts→RAG ────────────────────────────────────────────────


def test_index_alerts_hands_addressed_items_to_scheduler():
    from rugmunchbot.channels.scheduler import _posts_news

    captured: list[tuple[dict, str]] = []

    def _fake_schedule(payload, *, source):
        captured.append((payload, source))
        return object()  # truthy Task stand-in

    alerts = [
        {
            "address": "0xabc",
            "name": "ScamCoin",
            "symbol": "SCAM",
            "chain": "eth",
            "risk_score": 88,
            "risk_flags": ["honeypot"],
        },
        {"address": "", "name": "NoAddr"},  # skipped — no address
    ]
    with (
        patch.object(_posts_news, "schedule_scan_memory_index", _fake_schedule, create=True),
        # patch the symbol imported inside the function's lazy import path
        patch("rugmunchbot.tools.scanner.schedule_scan_memory_index", _fake_schedule),
    ):
        n = _posts_news._index_alerts_to_rag(alerts)

    assert n == 1
    payload, source = captured[0]
    assert source == "alert"
    assert payload["threats"] == ["honeypot"], "risk_flags remapped to threats"


def test_extract_scan_summary_accepts_alert_shape():
    """The alert payload shape must survive extract_scan_summary (W8)."""
    from rugmunchbot._vendor.rag_pkg.scan_memory import extract_scan_summary

    summary = extract_scan_summary(
        {
            "address": "0xabc",
            "name": "ScamCoin",
            "symbol": "SCAM",
            "chain": "eth",
            "risk_score": 88,
            "risk_level": "critical",
            "holders": 12,
            "liquidity": 1500.0,
            "threats": ["honeypot"],
        },
        source="alert",
    )
    assert summary["address"] == "0xabc"
    assert summary["risk_score"] == 88.0
    assert summary["source"] == "alert"
    assert summary["threats"] == ["honeypot"]


# ── Fix 4: per-channel style guides ─────────────────────────────────────


def test_style_guides_cover_strategy_channels():
    from rugmunchbot.channels.style_guides import CHANNEL_STYLE_GUIDES

    for ch in ("main", "alerts", "news", "alpha"):
        guide = CHANNEL_STYLE_GUIDES[ch]
        assert len(guide) > 80, f"{ch} guide too thin to be a real contract"


def test_style_guide_accessor_fails_soft():
    from rugmunchbot.channels.style_guides import CHANNEL_STYLE_GUIDES, style_guide

    assert style_guide("") == ""
    assert style_guide("unknown-channel") == ""
    assert style_guide("MAIN") == CHANNEL_STYLE_GUIDES["main"]


def test_rewrite_prompt_carries_channel_contract():
    """The rewrite prompt template accepts a {style} fragment."""
    from rugmunchbot.channels.rewrite_titles import _REWRITE_PROMPT

    rendered = _REWRITE_PROMPT.format(title="Test", style="\nCHANNEL: x\n")
    assert "CHANNEL: x" in rendered
    assert "Output ONLY the rewritten title" in rendered
    # and without a guide (empty fragment) it still renders cleanly
    rendered_plain = _REWRITE_PROMPT.format(title="Test", style="")
    assert "{style}" not in rendered_plain


@pytest.mark.asyncio
async def test_rewrite_news_title_passes_channel_guide():
    """rewrite_news_title injects the channel style guide into the prompt."""
    import rugmunchbot.channels.rewrite_titles as rt

    captured: dict = {}

    class _FakeLLM:
        async def __call__(self, prompt, *, user_id=0):
            captured["prompt"] = prompt
            return {"message": "Rewritten Headline"}

    fake = _FakeLLM()

    async def _fake_call(prompt, user_id=0):
        return await fake(prompt, user_id=user_id)

    orig_enabled = rt.TITLE_REWRITE_ENABLED
    rt.TITLE_REWRITE_ENABLED = True
    try:
        with patch("rugmunchbot.handlers.chat_ai._llm._call_remote_llm", _fake_call):
            out = await rt.rewrite_news_title("Some Headline", channel="news")
    finally:
        rt.TITLE_REWRITE_ENABLED = orig_enabled

    assert out == "Rewritten Headline"
    assert "@rmicryptonews" in captured["prompt"]
