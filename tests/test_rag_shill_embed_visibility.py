"""Visibility tests for the ct_shill_watch Qdrant embed (fakes only).

The shill upsert used to fail open at ``debug`` level. It must now return a
typed ``EmbedResult`` with a reason AND log a warning, while ``embed_shill``
keeps its ``bool`` contract for existing callers.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest
from rag_lane_fakes import DIMS, Resp, lane

from rugmunchbot.channels.ct_shill_watch import _embed as shill_embed


async def _vector(text: str) -> list[float]:
    return [0.1] * DIMS


async def _no_vector(text: str) -> list[float]:
    return []


class TestShillEmbedVisibility:
    @pytest.mark.asyncio
    async def test_embedder_failure_is_typed_and_loud(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch("rugmunchbot.channels.telegram_news._dedup._embed_candidate", _no_vector),
            caplog.at_level(logging.WARNING),
        ):
            out = await shill_embed.embed_shill_result({"handle": "@rug"})
        assert out.ok is False
        assert out.reason == "empty_vector"
        assert any("embed_skipped" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_missing_handle_is_typed(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            out = await shill_embed.embed_shill_result({"handle": ""})
        assert out.ok is False
        assert out.reason == "no_handle"
        assert any("embed_skipped" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_qdrant_rejection_is_typed(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch("rugmunchbot.channels.telegram_news._dedup._embed_candidate", _vector),
            lane(lambda url, kw: Resp({}, status=400)),
            caplog.at_level(logging.WARNING),
        ):
            out = await shill_embed.embed_shill_result({"handle": "@rug", "shill_count": 3})
        assert out.ok is False
        assert out.reason == "qdrant_http_400"
        assert out.dims == DIMS
        assert out.point_id
        assert any("qdrant_rejected" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_success_keeps_bool_contract(self) -> None:
        with (
            patch("rugmunchbot.channels.telegram_news._dedup._embed_candidate", _vector),
            lane(lambda url, kw: Resp({}, status=200)),
        ):
            assert await shill_embed.embed_shill({"handle": "@rug"}) is True

    @pytest.mark.asyncio
    async def test_transport_failure_is_typed(self, caplog: pytest.LogCaptureFixture) -> None:
        def boom(url: str, kw: dict[str, Any]) -> Any:
            raise RuntimeError("qdrant exploded")

        with (
            patch("rugmunchbot.channels.telegram_news._dedup._embed_candidate", _vector),
            lane(boom),
            caplog.at_level(logging.WARNING),
        ):
            out = await shill_embed.embed_shill_result({"handle": "@rug"})
        assert out.ok is False
        assert out.reason == "embed_failed:RuntimeError"
        assert any("embed_failed" in r.message for r in caplog.records)
