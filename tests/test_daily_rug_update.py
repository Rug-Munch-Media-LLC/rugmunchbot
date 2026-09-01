"""Tests for the daily rug update channel (channels/daily_rug_update/).

Covers: RAG context gathering (mocked rugbrain lanes), post generation
(LLM mocked), the deterministic quality gate (support/content_lint), the
feed-store mirror with the feed.rugmunch.io/p/{slug} deep link, and the
scheduler registration. All external calls (rugbrain HTTP, Telegram send,
LLM, Redis dedupe) are mocked.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.channels.daily_rug_update import (
    EDITION,
    _serialize_context,
    build_messages,
    cleanup_html,
    gather_rug_update_data,
    pick_update_image,
    post_daily_rug_update,
    quality_gate,
    rug_update_run_time,
)
from rugmunchbot.channels.scheduler import _core as sched_core


def _alert(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "SCAMTOKEN",
        "symbol": "SCAM",
        "chain": "solana",
        "severity": "critical",
        "risk_score": 92,
        "risk_flags": ["honeypot", "mint authority not revoked"],
        "url": "https://rugmunch.io/scan/SCAM",
        "image_url": "https://img.rugmunch.io/scam.png",
    }
    base.update(over)
    return base


def _campaign(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "token": "PEPE",
        "window_start": "2026-08-31T10:00:00Z",
        "window_end": "2026-08-31T16:00:00Z",
        "handles": ["@KolB", "kolA"],
        "kol_count": 3,
        "evidence_count": 4,
        "evidence": [{"url": "https://x.com/kolA/status/1"}],
    }
    base.update(over)
    return base


def _rag_hit(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "content": "Whale moved 12,000 ETH to an exchange an hour before the exploit.",
        "score": 0.9,
        "collection": "threat_intel_v2",
        "metadata": {
            "url": "https://rugmunch.io/intel/whale-move",
            "image_url": "https://img.rugmunch.io/whale.png",
        },
    }
    base.update(over)
    return base


def _context(**over: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "alerts": [_alert()],
        "campaigns": [_campaign()],
        "rag_intel": [_rag_hit()],
        "sources": ["alerts", "campaigns", "rag_intel"],
        "generated_at": "2026-08-31T16:30:00+00:00",
    }
    ctx.update(over)
    return ctx


@pytest.fixture(autouse=True)
def _clean_dedupe() -> Any:
    sched_core._posted_keys.clear()
    yield
    sched_core._posted_keys.clear()


class TestGather:
    @pytest.mark.asyncio
    async def test_gather_merges_all_lanes(self) -> None:
        with (
            patch(
                "rugmunchbot.channels.scanner_alerts.fetch_rag_alerts",
                new=AsyncMock(return_value=[_alert()]),
            ),
            patch(
                "rugmunchbot.channels.shill_campaign_post.fetch_shill_campaigns",
                new=AsyncMock(return_value=[_campaign()]),
            ),
            patch(
                "rugmunchbot.channels.daily_rug_update._fetch._fetch_rag_context",
                new=AsyncMock(return_value=[_rag_hit()]),
            ),
            patch(
                "rugmunchbot.channels.daily_rug_update._fetch._fetch_ranked_stories",
                new=AsyncMock(return_value=[]),
            ),
        ):
            data = await gather_rug_update_data()
        assert data["sources"] == ["alerts", "campaigns", "rag_intel"]
        assert data["alerts"][0]["symbol"] == "SCAM"
        assert data["campaigns"][0]["token"] == "PEPE"
        assert data["rag_intel"][0]["collection"] == "threat_intel_v2"

    @pytest.mark.asyncio
    async def test_gather_fail_open(self) -> None:
        with (
            patch(
                "rugmunchbot.channels.scanner_alerts.fetch_rag_alerts",
                new=AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch(
                "rugmunchbot.channels.shill_campaign_post.fetch_shill_campaigns",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "rugmunchbot.channels.daily_rug_update._fetch._fetch_rag_context",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "rugmunchbot.channels.daily_rug_update._fetch._fetch_ranked_stories",
                new=AsyncMock(return_value=[]),
            ),
        ):
            data = await gather_rug_update_data()
        assert data["sources"] == []
        assert data["alerts"] == []


class TestSerializeAndImage:
    def test_serialize_includes_all_blocks(self) -> None:
        text = _serialize_context(_context())
        assert "SCAMTOKEN" in text
        assert "PEPE" in text
        assert "threat_intel_v2" in text
        assert "https://rugmunch.io/scan/SCAM" in text

    def test_serialize_empty_context(self) -> None:
        assert _serialize_context({"alerts": [], "campaigns": [], "rag_intel": []}) == ""

    def test_pick_image_prefers_alert(self) -> None:
        assert pick_update_image(_context()) == "https://img.rugmunch.io/scam.png"

    def test_pick_image_falls_back_to_rag(self) -> None:
        ctx = _context(alerts=[_alert(image_url="")])
        assert pick_update_image(ctx) == "https://img.rugmunch.io/whale.png"

    def test_pick_image_none(self) -> None:
        ctx = _context(alerts=[_alert(image_url="")], rag_intel=[])
        assert pick_update_image(ctx) == ""


class TestBuildMessages:
    def test_prompt_has_edition_and_context(self) -> None:
        msgs = build_messages(_context())
        assert EDITION in msgs[0]["content"]
        assert "SCAMTOKEN" in msgs[1]["content"]
        assert "cite-or-omit" in msgs[1]["content"]

    def test_prompt_bans_slop(self) -> None:
        for banned in ("not financial advice", "DYOR", "stay safe out there"):
            assert banned in build_messages(_context())[0]["content"]


class TestQualityGate:
    def test_clean_post_passes(self) -> None:
        text = (
            "<b>━━━ The Daily Rug Update ━━━</b>\n\nSCAMTOKEN (SCAM) on Solana "
            "trapped buyers with a honeypot — risk score <b>92</b>. The mint "
            "authority was never revoked, so the deployer can print at will. "
            "That alone is a sell signal."
        )
        assert quality_gate(text, image_url="https://img.rugmunch.io/x.png") == []

    def test_banned_phrase_blocks(self) -> None:
        text = "This token looks risky, DYOR before aping. " + "x " * 30
        assert any("banned-phrase" in v for v in quality_gate(text))

    def test_double_link_blocks(self) -> None:
        url = "https://example.com/a"
        text = f"See {url} and again {url} plus commentary words here."
        assert any("double-link" in v for v in quality_gate(text))

    def test_missing_image_blocks(self) -> None:
        text = "Real commentary with plenty of words to satisfy the lint gate. " * 3
        assert any("missing-image" in v for v in quality_gate(text, image_url=""))


class TestCleanupAndSplit:
    def test_cleanup_converts_markdown(self) -> None:
        out = cleanup_html("## TOP RUGS\n**92%** risk")
        assert "<b>TOP RUGS</b>" in out
        assert "<b>92%</b>" in out

    def test_split_respects_budget(self) -> None:
        from rugmunchbot.channels.daily_rug_update._publish import _split_for_telegram

        report = "\n".join("x" * 100 for _ in range(100))
        parts = _split_for_telegram(report, max_chars=3900)
        assert all(len(p) <= 3900 for p in parts)
        assert "".join(p.replace("\n", "") for p in parts).startswith("x")


class TestPostDailyRugUpdate:
    @pytest.mark.asyncio
    async def test_full_pipeline_posts_and_mirrors(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.FEED_DATA", str(tmp_path))
        monkeypatch.setattr(sched_core, "_chat_id", lambda _name: "-100123")
        report = (
            "<b>━━━ The Daily Rug Update ━━━</b>\n\n"
            "<b>━━━ TOP RUGS &amp; SCAMS ━━━</b>\n\n"
            "SCAMTOKEN (SCAM) on Solana is today's textbook honeypot — risk "
            "score <b>92</b>, mint authority live. The deployer can mint at "
            "will; buyers are exit liquidity. Source: "
            '<a href="https://rugmunch.io/scan/SCAM">scanner</a>.'
        )
        llm = AsyncMock(return_value=report)
        send = AsyncMock(return_value=True)
        photo = AsyncMock(return_value=True)
        bot = MagicMock()
        ctx = MagicMock()
        ctx.bot = bot
        with (
            patch(
                "rugmunchbot.channels.daily_rug_update._publish.gather_rug_update_data",
                new=AsyncMock(return_value=_context()),
            ),
            patch("rugmunchbot.llm_config.chat_with_fallback", new=llm),
            patch.object(sched_core, "_safe_send", new=send),
            patch.object(sched_core, "_post_photo_aware", new=photo),
        ):
            assert await post_daily_rug_update(ctx) == 1
        # Mirror landed in the feed store with the image.
        posts = json.loads((tmp_path / "posts.json").read_text())
        assert posts[0]["kind"] == "rug-update"
        assert posts[0]["image"] == "https://img.rugmunch.io/scam.png"
        # Telegram got the post with the deep link to the exact mirrored post.
        sent_text = photo.call_args[0][2] if photo.call_args else send.call_args[0][2]
        assert f"https://rugmunch.io/blog/{posts[0]['slug']}" in str(sent_text)

    @pytest.mark.asyncio
    async def test_quality_gate_blocks_slop_post(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.FEED_DATA", str(tmp_path))
        monkeypatch.setattr(sched_core, "_chat_id", lambda _name: "-100123")
        slop = "Stay safe out there degens. " + "Not financial advice. " * 20
        llm = AsyncMock(return_value=slop)
        send = AsyncMock(return_value=True)
        ctx = MagicMock()
        with (
            patch(
                "rugmunchbot.channels.daily_rug_update._publish.gather_rug_update_data",
                new=AsyncMock(return_value=_context()),
            ),
            patch("rugmunchbot.llm_config.chat_with_fallback", new=llm),
            patch.object(sched_core, "_safe_send", new=send),
            patch.object(sched_core, "_post_photo_aware", new=AsyncMock(return_value=True)),
        ):
            assert await post_daily_rug_update(ctx) == 0
        send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_sources_skips(self, monkeypatch) -> None:
        monkeypatch.setattr(sched_core, "_chat_id", lambda _name: "-100123")
        with patch(
            "rugmunchbot.channels.daily_rug_update._publish.gather_rug_update_data",
            new=AsyncMock(
                return_value={"sources": [], "alerts": [], "campaigns": [], "rag_intel": []}
            ),
        ):
            assert await post_daily_rug_update(MagicMock()) == 0


class TestSchedule:
    def test_run_time_default_midday(self) -> None:
        t = rug_update_run_time()
        assert (t.hour, t.minute) == (12, 30)
        assert t.tzinfo is not None

    def test_run_time_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("RMI_DAILY_RUG_UPDATE_TIME", "13:15")
        import importlib

        from rugmunchbot.channels.daily_rug_update import _config

        importlib.reload(_config)
        t = _config.rug_update_run_time()
        assert (t.hour, t.minute) == (13, 15)
        importlib.reload(_config)  # restore defaults for other tests


class TestRegistration:
    def test_registered_in_scheduler(self) -> None:
        import inspect

        from rugmunchbot.channels.scheduler import _register

        src = inspect.getsource(_register.register_channel_jobs)
        assert "channel_midday_update" in src
        assert "post_daily_rug_update" in src
        assert "rug_update_run_time" in src
