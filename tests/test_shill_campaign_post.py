"""Tests for the shill-campaign channel poster (channels/shill_campaign_post.py).

Covers: campaign fetch from rugbrain, signature dedup, post format (one
source link, screenshot + commentary), banned-phrase style gate, and the
scheduler registration. All external calls (rugbrain HTTP, Telegram send,
Redis-backed dedupe) are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.channels import shill_campaign_post as scp
from rugmunchbot.channels.scheduler import _core as sched_core
from rugmunchbot.channels.shill_campaign_post import (
    _BANNED_PHRASES,
    SHILL_CAMPAIGN_MAX_POSTS,
    build_shill_campaign_post,
    campaign_signature,
    commentary,
    fetch_shill_campaigns,
    run_shill_campaign_poster,
)
from rugmunchbot.support.quality_gate import PLACEHOLDER_IMAGE_URL


def _campaign(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "token": "PEPE",
        "window_start": "2026-08-31T10:00:00Z",
        "window_end": "2026-08-31T16:00:00Z",
        "handles": ["@KolB", "kolA", "@KOLC"],
        "kol_count": 3,
        "evidence_count": 4,
        "evidence": [
            {"url": "https://x.com/kolA/status/1", "image_path": ""},
            {"url": "https://x.com/kolB/status/2", "image_path": ""},
        ],
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _clean_dedupe() -> Any:
    sched_core._posted_keys.clear()
    yield
    sched_core._posted_keys.clear()


class TestCampaignSignature:
    def test_stable_and_order_insensitive(self) -> None:
        a = campaign_signature(_campaign(handles=["@a", "@b", "@c"]))
        b = campaign_signature(_campaign(handles=["@c", "@a", "@b"]))
        assert a == b
        assert a

    def test_case_and_at_normalization(self) -> None:
        a = campaign_signature(_campaign(handles=["@KolA", "KOLB"]))
        b = campaign_signature(_campaign(handles=["kola", "@kolb"]))
        assert a == b

    def test_new_handle_changes_signature(self) -> None:
        a = campaign_signature(_campaign(handles=["@a", "@b"]))
        b = campaign_signature(_campaign(handles=["@a", "@b", "@c"]))
        assert a != b

    def test_new_window_changes_signature(self) -> None:
        a = campaign_signature(_campaign())
        b = campaign_signature(_campaign(window_start="2026-08-31T11:00:00Z"))
        assert a != b

    def test_empty_campaign_is_stable(self) -> None:
        assert campaign_signature({}) == campaign_signature({})


class TestCommentary:
    def test_no_banned_phrases_any_branch(self) -> None:
        for camp in (
            _campaign(kol_count=6, evidence_count=9),
            _campaign(kol_count=2, evidence_count=7),
            _campaign(kol_count=2, evidence_count=3),
        ):
            text = commentary(camp).lower()
            for banned in _BANNED_PHRASES:
                assert banned not in text, banned

    def test_big_cluster_branch(self) -> None:
        text = commentary(_campaign(kol_count=6, evidence_count=9))
        assert "6 separate call channels" in text

    def test_amplified_branch(self) -> None:
        text = commentary(_campaign(kol_count=2, evidence_count=7))
        assert "2 accounts, 7 posts" in text

    def test_default_branch(self) -> None:
        text = commentary(_campaign(kol_count=2, evidence_count=3))
        assert "2 accounts pushed this" in text

    def test_span_formatting_does_not_crash(self) -> None:
        # Regression: the >=5-KOL branch used "{span_txt:.0f}" on a str.
        text = commentary(_campaign(kol_count=5, evidence_count=5))
        assert "6h" in text

    def test_sub_hour_span(self) -> None:
        text = commentary(
            _campaign(window_start="2026-08-31T10:00:00Z", window_end="2026-08-31T10:30:00Z")
        )
        assert "under an hour" in text


class TestBuildPost:
    def test_shape_header_kols_evidence(self) -> None:
        text = build_shill_campaign_post(_campaign())
        assert "<b>Shill campaign: PEPE</b>" in text
        assert "KOLs (3): @KolB, @kolA, @KOLC" in text
        assert "Evidence: 4 posts" in text

    def test_exactly_one_source_link(self) -> None:
        text = build_shill_campaign_post(_campaign())
        assert text.count("Source: ") == 1
        assert "https://x.com/kolA/status/1" in text
        # second evidence URL never linked
        assert "kolB/status/2" not in text

    def test_non_http_urls_skipped(self) -> None:
        camp = _campaign(evidence=[{"url": "ftp://nope"}, {"url": "https://x.com/ok/1"}])
        assert "Source: https://x.com/ok/1" in build_shill_campaign_post(camp)

    def test_handle_truncation(self) -> None:
        camp = _campaign(handles=[f"kol{i}" for i in range(8)])
        text = build_shill_campaign_post(camp)
        assert "+2 more" in text

    def test_banned_phrase_raises(self) -> None:
        with (
            patch.object(scp, "commentary", return_value="Let's explore this DYOR moment"),
            pytest.raises(ValueError, match="style violation"),
        ):
            build_shill_campaign_post(_campaign())


class TestFetchCampaigns:
    async def test_posts_to_rugbrain_and_returns_campaigns(self) -> None:
        campaigns = [_campaign()]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"campaigns": campaigns}
        client = AsyncMock()
        client.post.return_value = resp
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        with patch.object(scp.httpx, "AsyncClient", return_value=client):
            out = await fetch_shill_campaigns(window_hours=24, min_kols=3)
        assert out == campaigns
        args, kwargs = client.post.call_args
        assert kwargs.get("json") == {"window_hours": 24, "min_kols": 3}
        assert (args[0] if args else kwargs.get("url", "")).endswith("/rag/shill-campaigns")

    async def test_non_200_yields_empty(self) -> None:
        resp = AsyncMock()
        resp.status_code = 503
        client = AsyncMock()
        client.post.return_value = resp
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False
        with patch.object(scp.httpx, "AsyncClient", return_value=client):
            assert await fetch_shill_campaigns() == []

    async def test_exception_fails_open(self) -> None:
        with patch.object(scp.httpx, "AsyncClient", side_effect=RuntimeError("down")):
            assert await fetch_shill_campaigns() == []


class _FakeBot:
    def __init__(self) -> None:
        self.photos: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    async def send_photo(self, **kw: Any) -> None:
        self.photos.append(kw)

    async def send_message(self, **kw: Any) -> None:
        self.messages.append(kw)


class _FakeCtx:
    def __init__(self, bot: _FakeBot) -> None:
        self.bot = bot


class TestRunPoster:
    def _patch_core(self, chat_id: str = "-100123") -> Any:
        return patch.multiple(
            sched_core,
            _chat_id=lambda channel: chat_id if channel == "main" else None,
        )

    async def test_posts_new_campaign_with_screenshot(self, tmp_path: Any) -> None:
        shot = tmp_path / "shot.png"
        shot.write_bytes(b"png")
        camp = _campaign(evidence=[{"url": "https://x.com/a/1", "image_path": str(shot)}])
        bot = _FakeBot()
        with (
            self._patch_core(),
            patch.object(scp, "fetch_shill_campaigns", AsyncMock(return_value=[camp])),
        ):
            posted = await run_shill_campaign_poster(ctx=_FakeCtx(bot))
        assert posted == 1
        assert len(bot.photos) == 1
        assert bot.photos[0]["chat_id"] == "-100123"
        assert "Shill campaign: PEPE" in bot.photos[0]["caption"]

    async def test_dedup_same_campaign_not_reposted(self) -> None:
        camp = _campaign()
        bot = _FakeBot()
        with (
            self._patch_core(),
            patch.object(scp, "fetch_shill_campaigns", AsyncMock(return_value=[camp])),
        ):
            assert await run_shill_campaign_poster(ctx=_FakeCtx(bot)) == 1
            assert await run_shill_campaign_poster(ctx=_FakeCtx(bot)) == 0
        assert len(bot.photos) + len(bot.messages) == 1

    async def test_placeholder_photo_when_no_screenshot(self) -> None:
        """Image doctrine (2026-08-31): no local screenshot → the gate's
        branded placeholder ships as photo + caption, never naked text."""
        camp = _campaign()
        bot = _FakeBot()
        with (
            self._patch_core(),
            patch.object(scp, "fetch_shill_campaigns", AsyncMock(return_value=[camp])),
        ):
            assert await run_shill_campaign_poster(ctx=_FakeCtx(bot)) == 1
        assert len(bot.photos) == 1
        assert bot.photos[0]["photo"] == PLACEHOLDER_IMAGE_URL
        assert not bot.messages

    async def test_max_posts_cap(self) -> None:
        camps = [_campaign(token=f"T{i}", handles=[f"@h{i}"]) for i in range(5)]
        bot = _FakeBot()
        with (
            self._patch_core(),
            patch.object(scp, "fetch_shill_campaigns", AsyncMock(return_value=camps)),
        ):
            assert await run_shill_campaign_poster(ctx=_FakeCtx(bot)) == SHILL_CAMPAIGN_MAX_POSTS

    async def test_failed_send_releases_key_for_retry(self) -> None:
        camp = _campaign()
        bot = _FakeBot()
        with (
            self._patch_core(),
            patch.object(scp, "fetch_shill_campaigns", AsyncMock(return_value=[camp])),
            patch(
                "rugmunchbot.channels._media.send_news_photo",
                AsyncMock(return_value=False),
            ),
            patch.object(sched_core, "_safe_send", AsyncMock(return_value=False)),
        ):
            assert await run_shill_campaign_poster(ctx=_FakeCtx(bot)) == 0
            # key released → next tick retries (and fails again)
            assert await run_shill_campaign_poster(ctx=_FakeCtx(bot)) == 0

    async def test_no_chat_id_noops(self) -> None:
        with patch.object(sched_core, "_chat_id", lambda channel: None):
            assert await run_shill_campaign_poster(ctx=_FakeCtx(_FakeBot())) == 0

    async def test_disabled_gate_returns_zero(self) -> None:
        with patch.object(scp, "SHILL_CAMPAIGN_ENABLED", False):
            assert await run_shill_campaign_poster(ctx=_FakeCtx(_FakeBot())) == 0

    async def test_malformed_campaign_skipped_not_fatal(self) -> None:
        bot = _FakeBot()
        # evidence_count that fails int() → build raises → campaign skipped
        with (
            self._patch_core(),
            patch.object(
                scp, "fetch_shill_campaigns", AsyncMock(return_value=[{"evidence_count": "abc"}])
            ),
        ):
            assert await run_shill_campaign_poster(ctx=_FakeCtx(bot)) == 0


class TestSchedulerRegistration:
    def test_campaign_poster_registered(self) -> None:
        from rugmunchbot.channels.scheduler._register import register_channel_jobs

        jobs: dict[str, dict[str, Any]] = {}

        class _JQ:
            def run_once(self, *a: Any, **k: Any) -> None:
                jobs[k.get("name", "")] = k

            def run_repeating(self, cb: Any, **k: Any) -> None:
                jobs[k.get("name", "")] = k

            def run_daily(self, cb: Any, **k: Any) -> None:
                jobs[k.get("name", "")] = k

        class _App:
            job_queue = _JQ()

        with patch.dict(
            "os.environ",
            {
                "RMI_CHANNELS_ENABLED": "true",
                "RMI_MAIN_CHAT_ID": "-100main",
                "RMI_ALERTS_CHAT_ID": "-100alerts",
                "RMI_ALPHA_CHAT_ID": "-100alpha",
                "RMI_NEWS_CHAT_ID": "-100news",
                "RMI_SCANS_CHAT_ID": "-100scans",
                "RMI_UPDATES_CHAT_ID": "-100upd",
                "RMI_PERSONAL_CHAT_ID": "-100pers",
            },
        ):
            register_channel_jobs(_App())  # type: ignore[arg-type]
        job = jobs.get("channel_shill_campaign")
        assert job, f"channel_shill_campaign not registered; have {sorted(jobs)}"
        assert job["interval"] == scp.SHILL_CAMPAIGN_INTERVAL_MIN * 60
