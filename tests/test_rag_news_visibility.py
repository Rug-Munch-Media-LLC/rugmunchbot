"""Visibility tests for the news RAG rails (fakes only, no network).

Covers the 2026-10-25 contract: a rerank outage falls back to ``rerank=False``
and is flagged ``degraded`` (so a post cannot claim RAG context it did not
get), an empty result set is DATA and not a degradation, and the TG-news
ingest counters always say ``ok`` / ``partial`` / ``failed`` / ``disabled``.

Fakes are installed through ``patch(target, value)`` on module attributes (the
same shape ``tests/test_news_rerank_lane.py`` uses) — no new monkeypatch sites.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from rugmunchbot.channels.content._rag_news import (
    fetch_rag_news_items,
    fetch_rag_news_result,
)


def _hit(title: str = "Whale moves ETH") -> dict[str, Any]:
    return {
        "title": title,
        "text": f"{title} — body",
        "metadata": {"source_url": "https://example.com/a", "source": "The Block"},
    }


class TestRerankFallback:
    @pytest.mark.asyncio
    async def test_success_marks_reranked(self) -> None:
        calls: list[bool] = []

        async def _search(
            query: str, collections: Any = None, limit: int = 10, rerank: bool = False
        ):
            calls.append(rerank)
            return [_hit()]

        with patch("rugmunchbot._vendor.rugbrain_client.rugbrain_search", _search):
            out = await fetch_rag_news_result(limit=1)
        assert calls == [True]
        assert out.degraded is False
        assert out.rerank is True
        assert out.items[0]["_rag_rerank"] is True
        assert out.items[0]["_rag_degraded"] is False
        assert out.items[0]["_rag_source"].startswith("rugbrain:")

    @pytest.mark.asyncio
    async def test_rerank_failure_falls_back_and_flags_degraded(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls: list[bool] = []

        async def _search(
            query: str, collections: Any = None, limit: int = 10, rerank: bool = False
        ):
            calls.append(rerank)
            if rerank:
                raise TimeoutError("reranker rack down")
            return [_hit()]

        with (
            patch("rugmunchbot._vendor.rugbrain_client.rugbrain_search", _search),
            caplog.at_level(logging.WARNING),
        ):
            out = await fetch_rag_news_result(limit=1)
        assert calls == [True, False]  # exactly one fallback attempt
        assert out.degraded is True
        assert out.reason == "rerank_unavailable"
        assert out.rerank is False
        assert out.items[0]["_rag_rerank"] is False  # cannot claim reranked context
        assert out.items[0]["_rag_degraded"] is True
        assert any("rag_news_degraded" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_total_failure_returns_no_items_and_reason(self) -> None:
        async def _search(
            query: str, collections: Any = None, limit: int = 10, rerank: bool = False
        ):
            raise ConnectionError("rugbrain down")

        with patch("rugmunchbot._vendor.rugbrain_client.rugbrain_search", _search):
            out = await fetch_rag_news_result(limit=3)
        assert out.items == []
        assert out.degraded is True
        assert out.reason.startswith("search_failed:")

    @pytest.mark.asyncio
    async def test_empty_result_is_data_not_degraded(self) -> None:
        async def _search(
            query: str, collections: Any = None, limit: int = 10, rerank: bool = False
        ):
            return []

        with patch("rugmunchbot._vendor.rugbrain_client.rugbrain_search", _search):
            out = await fetch_rag_news_result(limit=3)
        assert out.items == []
        assert out.degraded is False
        assert out.reason == ""

    @pytest.mark.asyncio
    async def test_list_accessor_unchanged(self) -> None:
        async def _search(
            query: str, collections: Any = None, limit: int = 10, rerank: bool = False
        ):
            return [_hit("A"), _hit("B")]

        with patch("rugmunchbot._vendor.rugbrain_client.rugbrain_search", _search):
            items = await fetch_rag_news_items(limit=5)
        assert [i["title"] for i in items] == ["A", "B"]


class TestTgNewsStatus:
    @pytest.mark.asyncio
    async def test_disabled_gate_is_typed(self) -> None:
        with patch("rugmunchbot.channels.telegram_news.TG_NEWS_RAG", False):
            import rugmunchbot.channels.telegram_news._rag as rag

            counts = await rag.ingest_into_rag([object()])  # type: ignore[list-item]
        assert counts["status"] == "disabled"
        assert counts["degraded"] is True
        assert counts["ingested"] == 0

    @pytest.mark.asyncio
    async def test_empty_input_is_not_degraded(self) -> None:
        import rugmunchbot.channels.telegram_news._rag as rag

        counts = await rag.ingest_into_rag([])
        assert counts["status"] == "empty"
        assert counts["degraded"] is False

    @pytest.mark.asyncio
    async def test_partial_ingest_is_flagged_degraded(self) -> None:
        import rugmunchbot.channels.telegram_news._rag as rag
        import rugmunchbot.channels.telegram_news._rag_stories as stories

        async def _counts(docs: Any, image: Any, collection: str) -> dict[str, Any]:
            return {"ingested": 1, "duplicates": 0, "errors": 2, "rejected_gate": 0}

        article = type(
            "A",
            (),
            {
                "body": "x" * 80,
                "title": "t",
                "source_channel": "c",
                "source_url": "u",
                "posted_at": None,
            },
        )()
        with (
            patch("rugmunchbot.channels.telegram_news.TG_NEWS_RAG", True),
            patch(
                "rugmunchbot.channels.telegram_news._rag._build_story_docs",
                lambda arts: [{"content": "c", "doc_id": "d", "metadata": {}}],
            ),
            patch("rugmunchbot.channels.telegram_news._rag.ingest_story_docs", _counts),
        ):
            counts = await rag.ingest_into_rag([article])
        assert counts["ingested"] == 1
        assert counts["errors"] == 2
        assert counts["status"] == "partial"
        assert counts["degraded"] is True
        assert stories.__name__  # module importable, loop lives there
