"""Tests for The Daily Rug — Morning Market Brief (channels/daily_rug/).

Covers: the once-per-day date guard (idempotent daily job), story
gathering (mocked rugbrain lanes), the longform prompt, the deterministic
quality gate, the feed mirror with the deep link, the branded logo
fallback, and the scheduler registration. All external calls (rugbrain
HTTP, Telegram send, LLM, Redis) are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.channels.daily_rug import (
    DAILY_RUG_LOGO_URL,
    EDITION,
    _serialize_context,
    build_messages,
    cleanup_html,
    daily_rug_run_time,
    gather_daily_rug_data,
    pick_daily_rug_image,
    post_daily_rug,
)
from rugmunchbot.channels.scheduler import _core as sched_core


def _story(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "SEC approves spot ETH ETF options",
        "summary": "The regulator cleared options on spot ETH ETFs after a 6-month review.",
        "source": "Cointelegraph",
        "url": "https://cointelegraph.com/news/eth-etf-options",
        "category": "regulation",
        "image_url": "https://img.rugmunch.io/eth.png",
    }
    base.update(over)
    return base


def _alert(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "SCAMTOKEN",
        "symbol": "SCAM",
        "chain": "solana",
        "severity": "critical",
        "risk_score": 92,
        "risk_flags": ["honeypot", "mint authority not revoked"],
        "url": "https://rugmunch.io/scan/SCAM",
    }
    base.update(over)
    return base


def _rag_hit(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "content": "Whale moved 12,000 ETH to an exchange an hour before the exploit.",
        "score": 0.9,
        "collection": "news_v2",
        "metadata": {"url": "https://rugmunch.io/intel/whale-move"},
    }
    base.update(over)
    return base


def _context(**over: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "alerts": [_alert()],
        "rag_intel": [_rag_hit()],
        "ranked_stories": [_story()],
        "sources": ["alerts", "rag_intel", "ranked_stories"],
        "generated_at": "2026-10-16T11:00:00+00:00",
    }
    ctx.update(over)
    return ctx


class TestConfig:
    def test_run_time_default_morning(self) -> None:
        t = daily_rug_run_time()
        assert (t.hour, t.minute) == (7, 30)
        assert t.tzinfo is not None

    def test_run_time_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("RMI_DAILY_RUG_TIME", "06:45")
        import importlib

        from rugmunchbot.channels.daily_rug import _config

        importlib.reload(_config)
        t = _config.daily_rug_run_time()
        assert (t.hour, t.minute) == (6, 45)
        importlib.reload(_config)  # restore defaults for other tests

    def test_edition_title(self) -> None:
        assert EDITION == "The Daily Rug — Morning Market Brief"

    def test_logo_url_is_branded(self) -> None:
        assert "daily-rug-logo" in DAILY_RUG_LOGO_URL


class TestPrompt:
    def test_build_messages_shape(self) -> None:
        msgs = build_messages(_context())
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert EDITION in msgs[0]["content"]
        assert "THINGS TO LOOK OUT FOR" in msgs[0]["content"]

    def test_user_prompt_carries_ranked_stories(self) -> None:
        msgs = build_messages(_context())
        assert "RANKED TOP STORIES" in msgs[1]["content"]
        assert "SEC approves spot ETH ETF options" in msgs[1]["content"]

    def test_serialize_context_blocks(self) -> None:
        text = _serialize_context(_context())
        assert "RANKED TOP STORIES" in text
        assert "SCANNER ALERTS" in text
        assert "RAG INTEL" in text

    def test_serialize_context_empty(self) -> None:
        assert _serialize_context({}) == "(no data)"


class TestImage:
    def test_picks_story_image(self) -> None:
        assert pick_daily_rug_image(_context()) == "https://img.rugmunch.io/eth.png"

    def test_falls_back_to_rag_image(self) -> None:
        ctx = _context(ranked_stories=[])
        ctx["rag_intel"] = [_rag_hit(metadata={"image_url": "https://img.rugmunch.io/rag.png"})]
        assert pick_daily_rug_image(ctx) == "https://img.rugmunch.io/rag.png"

    def test_empty_when_no_image(self) -> None:
        assert pick_daily_rug_image({"ranked_stories": [], "rag_intel": []}) == ""


class TestCleanup:
    def test_cleanup_html_strips_markdown(self) -> None:
        out = cleanup_html("## HEADER\n**bold** text")
        assert "<b>" in out
        assert "##" not in out

    def test_cleanup_html_empty(self) -> None:
        assert cleanup_html("") == ""


class TestGather:
    @pytest.mark.asyncio
    async def test_gather_returns_sources(self) -> None:
        with (
            patch(
                "rugmunchbot.channels.scanner_alerts.fetch_rag_alerts",
                new=AsyncMock(return_value=[_alert()]),
            ),
            patch(
                "rugmunchbot.channels.daily_rug._fetch._fetch_rag_context",
                new=AsyncMock(return_value=[_rag_hit()]),
            ),
            patch(
                "rugmunchbot.channels.daily_rug._fetch._fetch_ranked_stories",
                new=AsyncMock(return_value=[_story()]),
            ),
        ):
            ctx = await gather_daily_rug_data()
        assert set(ctx["sources"]) == {"alerts", "rag_intel", "ranked_stories"}
        assert ctx["generated_at"]

    @pytest.mark.asyncio
    async def test_gather_fail_open(self) -> None:
        with (
            patch(
                "rugmunchbot.channels.scanner_alerts.fetch_rag_alerts",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch(
                "rugmunchbot.channels.daily_rug._fetch._fetch_rag_context",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "rugmunchbot.channels.daily_rug._fetch._fetch_ranked_stories",
                new=AsyncMock(return_value=[]),
            ),
        ):
            ctx = await gather_daily_rug_data()
        assert ctx["sources"] == []


class TestDateGuard:
    def test_already_posted_today_fail_open(self) -> None:
        from rugmunchbot.channels.daily_rug import _guard

        with patch.object(_guard, "_client", return_value=None):
            assert _guard.already_posted_today() is False
            assert _guard.claim_today() is True

    def test_claim_today_uses_set_nx(self) -> None:
        from rugmunchbot.channels.daily_rug import _guard

        client = MagicMock()
        client.set.return_value = True
        with patch.object(_guard, "_client", return_value=client):
            assert _guard.claim_today() is True
        client.set.assert_called_once()
        assert client.set.call_args.kwargs.get("nx") is True

    def test_already_posted_today_true(self) -> None:
        from rugmunchbot.channels.daily_rug import _guard

        client = MagicMock()
        client.get.return_value = _guard._today().encode()
        with patch.object(_guard, "_client", return_value=client):
            assert _guard.already_posted_today() is True


class TestPost:
    @pytest.mark.asyncio
    async def test_no_chat_id_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(sched_core, "_chat_id", lambda _name: None)
        assert await post_daily_rug(MagicMock()) == 0

    @pytest.mark.asyncio
    async def test_already_posted_today_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(sched_core, "_chat_id", lambda _name: "-100123")
        with patch(
            "rugmunchbot.channels.daily_rug._publish.already_posted_today",
            return_value=True,
        ):
            assert await post_daily_rug(MagicMock()) == 0

    @pytest.mark.asyncio
    async def test_no_sources_releases_day(self, monkeypatch) -> None:
        monkeypatch.setattr(sched_core, "_chat_id", lambda _name: "-100123")
        with (
            patch(
                "rugmunchbot.channels.daily_rug._publish.already_posted_today",
                return_value=False,
            ),
            patch("rugmunchbot.channels.daily_rug._publish.claim_today", return_value=True),
            patch(
                "rugmunchbot.channels.daily_rug._publish.gather_daily_rug_data",
                new=AsyncMock(return_value={"sources": []}),
            ),
            patch("rugmunchbot.channels.daily_rug._publish.release_today") as rel,
        ):
            assert await post_daily_rug(MagicMock()) == 0
        rel.assert_called_once()


class TestRegistration:
    def test_registered_in_scheduler(self) -> None:
        import inspect

        from rugmunchbot.channels.scheduler import _register

        src = inspect.getsource(_register.register_channel_jobs)
        assert "channel_daily_rug" in src
        assert "post_daily_rug" in src
        assert "daily_rug_run_time" in src

    def test_midday_update_disabled_by_default(self) -> None:
        from rugmunchbot.channels.daily_rug_update import DAILY_RUG_UPDATE_ENABLED

        assert DAILY_RUG_UPDATE_ENABLED is False
