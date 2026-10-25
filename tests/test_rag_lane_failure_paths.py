"""Failure-path tests for the direct Qdrant lane (fakes only, no network).

Locks in the 2026-10-25 visibility contract: every lane failure is TYPED with a
reason, a wrong-width vector is REJECTED (never truncated), and an empty corpus
is distinguishable from a dead embedder or a dead collection.

Fakes are installed through ``patch(target, value)`` on module attributes (the
same shape ``tests/test_news_rerank_lane.py`` uses) — no new monkeypatch sites.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

import pytest

from rugmunchbot.channels.ct_shill_watch import _embed as shill_embed
from rugmunchbot.tools import rag_qdrant
from rugmunchbot.tools.rag_lookup import _content_key, _finalize_rag_lookup

_DIMS = 4096


class _Resp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.text = str(payload)[:160]

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    """One fake httpx client; ``handler(url, kw)`` returns a response or raises."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[str] = []

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def post(self, url: str, **kw: Any) -> _Resp:
        self.calls.append(url)
        return self._handler(url, kw)

    async def put(self, url: str, **kw: Any) -> _Resp:
        self.calls.append(url)
        return self._handler(url, kw)


class _Harness(ExitStack):
    """Install elementwise patches; every ``set`` is undone on exit."""

    def set(self, target: str, value: Any) -> None:
        self.enter_context(patch(target, value))


def _lane(handler: Any) -> _Harness:
    """Patch the shared httpx client + the corpus width for one test."""
    stack = _Harness()
    stack.set("rugmunchbot.tools.rag_qdrant.httpx.AsyncClient", lambda *a, **k: _Client(handler))
    stack.set("rugmunchbot.tools.rag_qdrant.expected_dims", lambda: _DIMS)
    stack.set("rugmunchbot.tools.rag_qdrant.rag_headers", dict)
    return stack


def _embed_ok(dims: int = _DIMS) -> Any:
    return lambda url, kw: _Resp({"embedding": [0.01] * dims})


def _search_ok(points: list[dict[str, Any]] | None = None) -> Any:
    pts = (
        points
        if points is not None
        else [
            {"id": "p1", "score": 0.9, "payload": {"content": "reentrancy pattern", "doc_id": "d1"}}
        ]
    )
    return lambda url, kw: _Resp({"result": pts})


class TestEmbedDimContract:
    @pytest.mark.asyncio
    async def test_embed_success(self) -> None:
        with _lane(_embed_ok()):
            out = await rag_qdrant.embed_query_outcome("what is reentrancy")
        assert out.ok is True
        assert out.reason == ""
        assert out.dims == _DIMS
        assert len(out.vector or []) == _DIMS

    @pytest.mark.asyncio
    async def test_dim_mismatch_is_rejected_never_truncated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with _lane(_embed_ok(dims=1024)), caplog.at_level(logging.WARNING):
            out = await rag_qdrant.embed_query_outcome("q")
        assert out.ok is False
        assert out.vector is None  # rejected, NOT truncated to 4096 or padded
        assert out.reason == f"dim_mismatch:1024!={_DIMS}"
        assert out.dims == 1024
        assert any("embed_dim_mismatch" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_empty_query_is_typed(self) -> None:
        with _lane(_embed_ok()):
            out = await rag_qdrant.embed_query_outcome("   ")
        assert out.reason == "empty_query"

    @pytest.mark.asyncio
    async def test_embed_unavailable_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        def boom(url: str, kw: dict[str, Any]) -> _Resp:
            raise ConnectionError("embed down")

        with _lane(boom), caplog.at_level(logging.WARNING):
            out = await rag_qdrant.embed_query_outcome("q")
        assert out.ok is False
        assert out.reason.startswith("embed_unavailable")
        assert any("embed_unavailable" in r.message for r in caplog.records)


class TestLaneFailures:
    @pytest.mark.asyncio
    async def test_embed_failure_marks_lane_degraded_typed(self) -> None:
        with _lane(_embed_ok(dims=512)):
            lane = await rag_qdrant.direct_qdrant_outcome("q", ["security_patterns"], 5)
        assert lane.hits == []
        assert lane.ok is False
        assert lane.degraded is True
        assert lane.reason.startswith("dim_mismatch")

    @pytest.mark.asyncio
    async def test_qdrant_timeout_is_degraded(self, caplog: pytest.LogCaptureFixture) -> None:
        def handler(url: str, kw: dict[str, Any]) -> _Resp:
            if url.endswith("/api/v1/embed"):
                return _embed_ok()(url, kw)
            raise TimeoutError("qdrant read timeout")

        with _lane(handler), caplog.at_level(logging.WARNING):
            lane = await rag_qdrant.direct_qdrant_outcome("q", ["scam_patterns"], 5)
        assert lane.ok is False
        assert lane.degraded is True
        assert "TimeoutError" in lane.reason
        assert any("collection_unavailable" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_qdrant_unavailable_is_degraded(self) -> None:
        import httpx

        def handler(url: str, kw: dict[str, Any]) -> _Resp:
            if url.endswith("/api/v1/embed"):
                return _embed_ok()(url, kw)
            raise httpx.ConnectError("qdrant refused")

        with _lane(handler):
            lane = await rag_qdrant.direct_qdrant_outcome("q", ["scam_patterns"], 5)
        assert lane.degraded is True
        assert lane.ok is False
        assert "search_unavailable" in lane.reason

    @pytest.mark.asyncio
    async def test_partial_collection_failure_is_degraded(self) -> None:
        def handler(url: str, kw: dict[str, Any]) -> _Resp:
            if url.endswith("/api/v1/embed"):
                return _embed_ok()(url, kw)
            if "security_patterns" in url:
                return _search_ok()(url, kw)
            return _Resp({}, status=503)

        with _lane(handler):
            lane = await rag_qdrant.direct_qdrant_outcome(
                "q", ["security_patterns", "defi_exploits"], 5
            )
        assert len(lane.hits) == 1
        assert lane.ok is True
        assert lane.degraded is True  # partial coverage is never silently ok
        assert "search_http_503" in lane.reason

    @pytest.mark.asyncio
    async def test_lane_timeout_marks_degraded(self) -> None:
        def handler(url: str, kw: dict[str, Any]) -> _Resp:
            return _embed_ok()(url, kw) if url.endswith("/api/v1/embed") else _search_ok()(url, kw)

        with _lane(handler) as h:
            h.set("rugmunchbot.tools.rag_qdrant.RAG_LOOKUP_TIMEOUT", 0.0)
            lane = await rag_qdrant.direct_qdrant_outcome("q", ["security_patterns"], 5)
        assert lane.ok is False
        assert lane.degraded is True
        assert lane.reason == "search_timeout"

    @pytest.mark.asyncio
    async def test_empty_corpus_is_not_degraded(self, caplog: pytest.LogCaptureFixture) -> None:
        def handler(url: str, kw: dict[str, Any]) -> _Resp:
            return (
                _embed_ok()(url, kw) if url.endswith("/api/v1/embed") else _search_ok([])(url, kw)
            )

        with _lane(handler), caplog.at_level(logging.INFO):
            lane = await rag_qdrant.direct_qdrant_outcome("q", ["security_patterns"], 5)
        assert lane.hits == []
        assert lane.degraded is False
        assert lane.ok is False
        assert lane.reason == ""
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


class TestShillEmbedVisibility:
    @pytest.mark.asyncio
    async def test_embedder_failure_is_typed_and_loud(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _no_vector(text: str) -> list[float]:
            return []

        with (
            patch("rugmunchbot.channels.telegram_news._dedup._embed_candidate", _no_vector),
            caplog.at_level(logging.WARNING),
        ):
            out = await shill_embed.embed_shill_result({"handle": "@rug"})
        assert out.ok is False
        assert out.reason == "empty_vector"
        assert any("embed_skipped" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_qdrant_rejection_is_typed(self, caplog: pytest.LogCaptureFixture) -> None:
        async def _vector(text: str) -> list[float]:
            return [0.1] * _DIMS

        with (
            patch("rugmunchbot.channels.telegram_news._dedup._embed_candidate", _vector),
            _lane(lambda url, kw: _Resp({}, status=400)),
            caplog.at_level(logging.WARNING),
        ):
            out = await shill_embed.embed_shill_result({"handle": "@rug", "shill_count": 3})
        assert out.ok is False
        assert out.reason == "qdrant_http_400"
        assert out.dims == _DIMS
        assert out.point_id
        assert any("qdrant_rejected" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_success_keeps_bool_contract(self) -> None:
        async def _vector(text: str) -> list[float]:
            return [0.1] * _DIMS

        with (
            patch("rugmunchbot.channels.telegram_news._dedup._embed_candidate", _vector),
            _lane(lambda url, kw: _Resp({}, status=200)),
        ):
            assert await shill_embed.embed_shill({"handle": "@rug"}) is True
