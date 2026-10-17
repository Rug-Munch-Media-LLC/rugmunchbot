"""Tests for the shared reranker story-selection lane (channels/_news_rerank).

Covers: window computation from scheduler state (counterpart jobs, fallback,
clamping), the rugbrain news-rerank HTTP lane (mocked), timeframe filtering
(stale dropped, undated kept), prompt-block serialization, and the ranked
lanes wired into both flagship posts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.channels import _news_rerank as nr


def _item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Whale moves 12,000 ETH",
        "summary": "A whale moved 12,000 ETH to an exchange.",
        "source": "The Block",
        "url": "https://example.com/whale",
        "published_at": datetime.now(UTC).isoformat(),
        "score": 0.91,
        "category": "markets",
    }
    base.update(over)
    return base


# ── window computation ──────────────────────────────────────────────


class TestWindow:
    def test_morning_window_uses_midday_last_run(self) -> None:
        fake = MagicMock()
        fake.last_run.side_effect = lambda job: (
            datetime.now(UTC) - timedelta(hours=5, minutes=30)
            if job == "channel_midday_update"
            else None
        )
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert 5.0 <= nr.morning_window_hours() <= 6.0

    def test_midday_window_uses_morning_last_run(self) -> None:
        fake = MagicMock()
        fake.last_run.side_effect = lambda job: (
            datetime.now(UTC) - timedelta(hours=2) if job == "channel_morning_rug" else None
        )
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert 1.5 <= nr.midday_window_hours() <= 2.5

    def test_fallback_12h_when_never_run(self) -> None:
        fake = MagicMock()
        fake.last_run.return_value = None
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert nr.morning_window_hours() == 12.0
            assert nr.midday_window_hours() == 12.0

    def test_window_clamped_to_max(self) -> None:
        fake = MagicMock()
        fake.last_run.return_value = datetime.now(UTC) - timedelta(days=10)
        with patch("rugmunchbot.channels.scheduler_state.get_state", return_value=fake):
            assert nr.window_hours_since(("channel_morning_rug",)) == nr.MAX_WINDOW_HOURS

    def test_window_fail_open_on_state_error(self) -> None:
        with patch(
            "rugmunchbot.channels.scheduler_state.get_state", side_effect=RuntimeError("boom")
        ):
            assert nr.window_hours_since(("x",)) == 12.0


# ── HTTP lane ───────────────────────────────────────────────────────


class _Resp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(self, resp: _Resp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    # The lane is GET ``/news/top`` (2026-10-17 — the POST ``/rag/news-rerank``
    # endpoint was never implemented server-side and 404'd on every run).
    async def get(self, *a: Any, **k: Any) -> _Resp:
        return self._resp


def _article(**over: Any) -> dict[str, Any]:
    """A raw ``/news/top`` article row (the backend's own shape)."""
    base: dict[str, Any] = {
        "headline": "Whale moves 12,000 ETH",
        "title": "Whale moves 12,000 ETH",
        "published_at": datetime.now(UTC).isoformat(),
        "url": "https://example.com/whale",
        "source": "The Block",
        "quality": 0.91,
        "ranked_by": "rerank",
    }
    base.update(over)
    return base


class TestFetchTopRanked:
    @pytest.mark.asyncio
    async def test_filters_stale_keeps_undated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_API_URL", "http://rag.test/api/v1")
        monkeypatch.setenv("RMI_RAG_API_KEY", "k1,k0")
        fresh = _article()
        stale = _article(
            headline="Old",
            title="Old",
            url="https://example.com/old",
            published_at=(datetime.now(UTC) - timedelta(hours=30)).isoformat(),
        )
        undated = _article(
            headline="No date", title="No date", url="https://example.com/nodate", published_at=""
        )
        resp = _Client(_Resp({"articles": [fresh, stale, undated]}))
        with patch("rugmunchbot.channels._news_rerank.httpx.AsyncClient", return_value=resp):
            out = await nr.fetch_top_ranked_stories(window_hours=12.0)
        titles = [i["title"] for i in out]
        assert "Whale moves 12,000 ETH" in titles
        assert "No date" in titles  # undated kept, flagged
        assert out[-1].get("_undated") is True
        assert "Old" not in titles  # provably stale dropped

    @pytest.mark.asyncio
    async def test_fail_open_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_API_URL", "http://rag.test/api/v1")
        monkeypatch.setenv("RMI_RAG_API_KEY", "k1")
        resp = _Client(_Resp({}, status=503))
        with patch("rugmunchbot.channels._news_rerank.httpx.AsyncClient", return_value=resp):
            assert await nr.fetch_top_ranked_stories() == []

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_API_URL", "http://rag.test/api/v1")
        monkeypatch.setenv("RMI_RAG_API_KEY", "k1")

        class _Boom:
            async def __aenter__(self) -> _Boom:
                raise ConnectionError("down")

            async def __aexit__(self, *a: Any) -> None:
                return None

        with patch("rugmunchbot.channels._news_rerank.httpx.AsyncClient", return_value=_Boom()):
            assert await nr.fetch_top_ranked_stories() == []

    @pytest.mark.asyncio
    async def test_no_config_returns_empty(self) -> None:
        # The resolver chain is env → secret file → gopass, so "no config"
        # means the WHOLE chain is empty — patch the canonical resolver rather
        # than the env var (a machine with the on-disk secret file must not
        # make this test depend on that file being absent).
        with patch("rugmunchbot.tools.rag_lookup._rag_api_key", return_value=""):
            assert await nr.fetch_top_ranked_stories() == []

    @pytest.mark.asyncio
    async def test_rotation_key_sends_active_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_API_URL", "http://rag.test/api/v1")
        monkeypatch.setenv("RMI_RAG_API_KEY", "active,old")
        captured: dict[str, Any] = {}

        class _Cap(_Client):
            async def get(self, url: str, headers: dict[str, str], **k: Any) -> _Resp:
                captured["url"] = url
                captured["headers"] = headers
                return _Resp({"articles": []})

        with patch(
            "rugmunchbot.channels._news_rerank.httpx.AsyncClient",
            return_value=_Cap(_Resp({"articles": []})),
        ):
            await nr.fetch_top_ranked_stories()
        assert captured["url"] == "http://rag.test/api/v1/news/top"
        assert captured["headers"]["X-API-Key"] == "active"


class TestPromptBlock:
    def test_serializes_items(self) -> None:
        block = nr.to_prompt_block([_item()], label="RANKED TOP STORIES")
        assert block.startswith("RANKED TOP STORIES:")
        assert "Whale moves 12,000 ETH" in block
        assert "[url=https://example.com/whale]" in block

    def test_empty_items_empty_block(self) -> None:
        assert nr.to_prompt_block([], label="X") == ""


# ── wiring into the flagship posts ──────────────────────────────────


class TestPostWiring:
    @pytest.mark.asyncio
    async def test_morning_rug_ranked_stories_override_news(self) -> None:
        from rugmunchbot.channels.daily_market_summary import _assemble

        ranked = [_item()]
        with (
            patch.object(_assemble, "_fetch_major_prices", new=AsyncMock(return_value={})),
            patch.object(_assemble, "_fetch_market_condition", new=AsyncMock(return_value={})),
            patch.object(_assemble, "_fetch_trending", new=AsyncMock(return_value=[])),
            patch.object(_assemble, "_fetch_movers", new=AsyncMock(return_value={})),
            patch.object(_assemble, "_fetch_news", new=AsyncMock(return_value=[])),
            patch.object(_assemble, "_fetch_global_metrics", new=AsyncMock(return_value={})),
            patch.object(_assemble, "_fetch_rag_intel", new=AsyncMock(return_value=[])),
            patch(
                "rugmunchbot.channels._news_rerank.fetch_top_ranked_stories",
                new=AsyncMock(return_value=ranked),
            ),
            patch(
                "rugmunchbot.channels._news_rerank.morning_window_hours",
                return_value=12.0,
            ),
        ):
            data = await _assemble.gather_summary_data()
        assert data["ranked_stories"] == ranked
        assert data["news"] == _assemble._map_ranked_items(ranked)
        assert "ranked_stories" in data["sources"]
        serialized = _assemble._serialize_context(data)
        assert "RANKED TOP STORIES" in serialized

    @pytest.mark.asyncio
    async def test_midday_update_ranked_stories_lane(self) -> None:
        from rugmunchbot.channels.daily_rug_update import _fetch

        ranked = [_item()]
        with (
            patch(
                "rugmunchbot.channels.scanner_alerts.fetch_rag_alerts",
                new=AsyncMock(return_value=[]),
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
                new=AsyncMock(return_value=ranked),
            ),
        ):
            data = await _fetch.gather_rug_update_data()
        assert data["ranked_stories"] == ranked
        assert "ranked_stories" in data["sources"]
        serialized = _fetch._serialize_context(data)
        assert "RANKED TOP STORIES" in serialized

    @pytest.mark.asyncio
    async def test_ranked_lane_fail_open(self) -> None:
        from rugmunchbot.channels.daily_rug_update import _fetch

        with patch(
            "rugmunchbot.channels._news_rerank.fetch_top_ranked_stories",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            assert await _fetch._fetch_ranked_stories() == []


class TestBranding:
    def test_edition_names(self) -> None:
        from rugmunchbot.channels.daily_market_summary import _publish as ms
        from rugmunchbot.channels.daily_rug_update import _publish as ru

        assert ms.MORNING_EDITION == "The Morning Rug"
        assert ms.MIDDAY_EDITION == "Midday Market Update"
        assert ru.EDITION == "Midday Market Update"
        assert "The Morning Rug" in ms.SYSTEM_PROMPT
        # daily_rug_update injects its edition via {EDITION}
        msgs = ru.build_messages({"generated_at": "2026-08-31T12:30:00+00:00"})
        assert "Midday Market Update" in msgs[0]["content"]
        assert "Midday Market Update" in msgs[1]["content"]

    def test_scheduler_job_names(self) -> None:
        import inspect

        from rugmunchbot.channels.scheduler import _register

        src = inspect.getsource(_register)
        assert 'name="channel_morning_rug"' in src
        assert 'name="channel_midday_update"' in src
        assert 'name="daily_market_summary"' not in src
        assert 'name="channel_daily_rug_update"' not in src
