"""Tests for the story-level RAG ingest loop (fakes only, no network).

Covers the report -> counter mapping that decides whether a sweep reports
``ok``, ``partial`` or ``failed``: accepted, duplicate, quality-gate rejection,
pipeline error, and a raised exception — plus the image backfill.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from rugmunchbot.channels.telegram_news._rag_stories import ingest_story_docs


class _Report:
    def __init__(
        self,
        accepted: int = 0,
        duplicate: int = 0,
        rejected: int = 0,
        errors: list[str] | None = None,
        status: str = "ok",
        rejections: dict[str, int] | None = None,
    ) -> None:
        self.accepted = accepted
        self.duplicate = duplicate
        self.rejected = rejected
        self.errors = errors or []
        self.status = status
        self.rejections = rejections or {}


class _Ingestor:
    def __init__(self, behaviour: list[Any]) -> None:
        self.behaviour = behaviour
        self.docs: list[dict[str, Any]] = []

    async def ingest(self, **kw: Any) -> _Report:
        self.docs.append(kw)
        out = self.behaviour[len(self.docs) - 1]
        if isinstance(out, BaseException):
            raise out
        return out


def _doc(doc_id: str = "d1", **meta: Any) -> dict[str, Any]:
    return {"content": "story body", "doc_id": doc_id, "metadata": dict(meta)}


async def _run(
    behaviour: list[Any], docs: list[dict[str, Any]], images: dict[str, str] | None = None
) -> tuple[dict[str, Any], _Ingestor]:
    ingestor = _Ingestor(behaviour)
    with patch("rugmunchbot._vendor.rag_pkg.ingestion.BatchIngestor", lambda: ingestor):
        counts = await ingest_story_docs(docs, images or {}, "news_v2")
    return counts, ingestor


class TestIngestStoryDocs:
    @pytest.mark.asyncio
    async def test_accepted_and_duplicate(self) -> None:
        counts, ingestor = await _run(
            [_Report(accepted=1), _Report(duplicate=1)], [_doc("a"), _doc("b")]
        )
        assert counts["ingested"] == 1
        assert counts["duplicates"] == 1
        assert counts["errors"] == 0
        assert len(ingestor.docs) == 2
        assert {d["collection"] for d in ingestor.docs} == {"news_v2"}

    @pytest.mark.asyncio
    async def test_quality_gate_rejection_is_not_an_error(self) -> None:
        counts, _ = await _run([_Report(rejected=1, status="rejected")], [_doc()])
        assert counts["rejected_gate"] == 1
        assert counts["errors"] == 0

    @pytest.mark.asyncio
    async def test_pipeline_error_counts_errors(self) -> None:
        counts, _ = await _run(
            [_Report(errors=["embed down"], status="error", rejections={"empty": 2})], [_doc()]
        )
        assert counts["errors"] >= 1
        assert counts["ingested"] == 0

    @pytest.mark.asyncio
    async def test_raised_exception_is_counted_not_propagated(self) -> None:
        counts, _ = await _run([TimeoutError("qdrant")], [_doc()])
        assert counts["errors"] == 1

    @pytest.mark.asyncio
    async def test_image_backfill_uses_member_url(self) -> None:
        counts, ingestor = await _run(
            [_Report(accepted=1)],
            [_doc(source_urls=["https://x/1"])],
            {"https://x/1": "https://img/1"},
        )
        assert counts["ingested"] == 1
        assert ingestor.docs[0]["metadata"]["image_url"] == "https://img/1"

    @pytest.mark.asyncio
    async def test_existing_image_is_not_overwritten(self) -> None:
        _, ingestor = await _run(
            [_Report(accepted=1)],
            [_doc(source_urls=["https://x/1"], image_url="https://img/own")],
            {"https://x/1": "https://img/1"},
        )
        assert ingestor.docs[0]["metadata"]["image_url"] == "https://img/own"

    @pytest.mark.asyncio
    async def test_empty_input_is_a_noop(self) -> None:
        counts = await ingest_story_docs([], {}, "news_v2")
        assert counts["ingested"] == 0
        assert counts["errors"] == 0
