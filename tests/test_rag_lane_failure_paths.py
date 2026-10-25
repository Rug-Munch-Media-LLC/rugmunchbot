"""Failure-path tests for the direct Qdrant lane (fakes only, no network).

Locks in the 2026-10-25 visibility contract: every lane failure is TYPED with a
reason, a wrong-width vector is REJECTED (never truncated), and an empty corpus
is distinguishable from a dead embedder or a dead collection.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from rag_lane_fakes import DIMS, Resp, embed_ok, lane, search_ok

from rugmunchbot.tools import rag_qdrant
from rugmunchbot.tools.rag_lookup import _content_key, _finalize_rag_lookup


class TestEmbedDimContract:
    @pytest.mark.asyncio
    async def test_embed_success(self) -> None:
        with lane(embed_ok()):
            out = await rag_qdrant.embed_query_outcome("what is reentrancy")
        assert out.ok is True
        assert out.reason == ""
        assert out.dims == DIMS
        assert len(out.vector or []) == DIMS

    @pytest.mark.asyncio
    async def test_embed_endpoint_url(self) -> None:
        with lane(embed_ok()):
            await rag_qdrant.embed_query_outcome("q")
        assert rag_qdrant.embed_url().endswith("/api/v1/embed")

    @pytest.mark.asyncio
    async def test_dim_mismatch_is_rejected_never_truncated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with lane(embed_ok(dims=1024)), caplog.at_level(logging.WARNING):
            out = await rag_qdrant.embed_query_outcome("q")
        assert out.ok is False
        assert out.vector is None  # rejected, NOT truncated to 4096 or padded
        assert out.reason == f"dim_mismatch:1024!={DIMS}"
        assert out.dims == 1024
        assert any("embed_dim_mismatch" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_empty_query_is_typed(self) -> None:
        with lane(embed_ok()):
            out = await rag_qdrant.embed_query_outcome("   ")
        assert out.reason == "empty_query"

    @pytest.mark.asyncio
    async def test_embed_unavailable_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        def boom(url: str, kw: dict[str, Any]) -> Any:
            raise ConnectionError("embed down")

        with lane(boom), caplog.at_level(logging.WARNING):
            out = await rag_qdrant.embed_query_outcome("q")
        assert out.ok is False
        assert out.reason.startswith("embed_unavailable")
        assert any("embed_unavailable" in r.message for r in caplog.records)


class TestLaneFailures:
    @pytest.mark.asyncio
    async def test_embed_failure_marks_lane_degraded_typed(self) -> None:
        with lane(embed_ok(dims=512)):
            out = await rag_qdrant.direct_qdrant_outcome("q", ["security_patterns"], 5)
        assert out.hits == []
        assert out.ok is False
        assert out.degraded is True
        assert out.reason.startswith("dim_mismatch")

    @pytest.mark.asyncio
    async def test_qdrant_timeout_is_degraded(self, caplog: pytest.LogCaptureFixture) -> None:
        def handler(url: str, kw: dict[str, Any]) -> Any:
            if url.endswith("/api/v1/embed"):
                return embed_ok()(url, kw)
            raise TimeoutError("qdrant read timeout")

        with lane(handler), caplog.at_level(logging.WARNING):
            out = await rag_qdrant.direct_qdrant_outcome("q", ["scam_patterns"], 5)
        assert out.ok is False
        assert out.degraded is True
        assert "TimeoutError" in out.reason
        assert any("collection_unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_qdrant_unavailable_is_degraded(self) -> None:
        import httpx

        def handler(url: str, kw: dict[str, Any]) -> Any:
            if url.endswith("/api/v1/embed"):
                return embed_ok()(url, kw)
            raise httpx.ConnectError("qdrant refused")

        with lane(handler):
            out = await rag_qdrant.direct_qdrant_outcome("q", ["scam_patterns"], 5)
        assert out.degraded is True
        assert out.ok is False
        assert "search_unavailable" in out.reason

    @pytest.mark.asyncio
    async def test_partial_collection_failure_is_degraded(self) -> None:
        def handler(url: str, kw: dict[str, Any]) -> Any:
            if url.endswith("/api/v1/embed"):
                return embed_ok()(url, kw)
            if "security_patterns" in url:
                return search_ok()(url, kw)
            return Resp({}, status=503)

        with lane(handler):
            out = await rag_qdrant.direct_qdrant_outcome(
                "q", ["security_patterns", "defi_exploits"], 5
            )
        assert len(out.hits) == 1
        assert out.ok is True
        assert out.degraded is True  # partial coverage is never silently ok
        assert "search_http_503" in out.reason

    @pytest.mark.asyncio
    async def test_lane_timeout_marks_degraded(self) -> None:
        def handler(url: str, kw: dict[str, Any]) -> Any:
            return embed_ok()(url, kw) if url.endswith("/api/v1/embed") else search_ok()(url, kw)

        with lane(handler) as stack:
            stack.set("rugmunchbot.tools.rag_qdrant.RAG_LOOKUP_TIMEOUT", 0.0)
            out = await rag_qdrant.direct_qdrant_outcome("q", ["security_patterns"], 5)
        assert out.ok is False
        assert out.degraded is True
        assert out.reason == "search_timeout"

    @pytest.mark.asyncio
    async def test_empty_corpus_is_not_degraded(self, caplog: pytest.LogCaptureFixture) -> None:
        def handler(url: str, kw: dict[str, Any]) -> Any:
            return embed_ok()(url, kw) if url.endswith("/api/v1/embed") else search_ok([])(url, kw)

        with lane(handler), caplog.at_level(logging.INFO):
            out = await rag_qdrant.direct_qdrant_outcome("q", ["security_patterns"], 5)
        assert out.hits == []
        assert out.degraded is False
        assert out.ok is False
        assert out.reason == ""
        assert any("lane_empty" in r.message for r in caplog.records)


class TestDedupeByContentHash:
    def test_same_content_same_key_regardless_of_collection(self) -> None:
        a = {"content": "Reentrancy  guard", "collection": "security_patterns"}
        b = {"content": "reentrancy guard", "collection": "contract_security"}
        assert _content_key(a) == _content_key(b)

    def test_opposite_verdicts_never_collapse(self) -> None:
        clean = {"content": "same code", "verdict": "clean"}
        bad = {"content": "same code", "verdict": "malicious"}
        assert _content_key(clean) != _content_key(bad)

    def test_finalize_keeps_best_copy_of_each_snippet(self) -> None:
        hits = [
            {"content": "dup snippet", "score": 0.2, "collection": "a"},
            {"content": "dup snippet ", "score": 0.9, "collection": "b"},
            {"content": "other snippet", "score": 0.5, "collection": "c"},
        ]
        result = _finalize_rag_lookup("q", hits, [], [])
        assert result is not None
        assert len(result.data["hits"]) == 2
        assert result.data["hits"][0]["score"] == 0.9
