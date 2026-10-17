"""Regression — the midday report must not post zero (2026-10-17 incident).

Two root causes, both pinned here:

1. ``_fetch_macro_stories`` dropped undated stories (Telegram reranker items
   carry no ``published_at``) and returned [] when nothing matched the macro
   facet, leaving the writer with no citations and no links.
2. The writer returned the 4-char ``SKIP`` sentinel twice; the pipeline logged
   ``too_short_skipped`` instead of an explicit skip, and the prompt demanded
   3 links it had not been given.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from rugmunchbot.channels.midday_market_report._fetch import (
    _fetch_macro_stories,
    count_context_links,
)
from rugmunchbot.channels.midday_market_report._pipeline import is_skip
from rugmunchbot.channels.midday_market_report._prompt import build_messages
from rugmunchbot.channels.midday_market_report._write import write_report


def _story(title: str, url: str = "", published: object = None) -> dict[str, object]:
    return {
        "title": title,
        "summary": "",
        "source": "TG",
        "url": url,
        "published_at": published,
    }


@pytest.mark.asyncio
async def test_undated_macro_story_is_kept() -> None:
    stories = [_story("Fed holds rates as CPI cools"), _story("Solana memecoin launches")]
    with patch(
        "rugmunchbot.channels._news_rerank.fetch_top_ranked_stories",
        AsyncMock(return_value=stories),
    ):
        out = await _fetch_macro_stories()
    assert [s["title"] for s in out] == ["Fed holds rates as CPI cools"]


@pytest.mark.asyncio
async def test_macro_dry_window_falls_back_to_ranked_stories() -> None:
    stories = [_story("Solana memecoin launches"), _story("Whale moves 4k BTC")]
    with patch(
        "rugmunchbot.channels._news_rerank.fetch_top_ranked_stories",
        AsyncMock(return_value=stories),
    ):
        out = await _fetch_macro_stories()
    assert len(out) == 2


def test_skip_sentinel_detected() -> None:
    assert is_skip("SKIP")
    assert is_skip("  skip. ")
    assert not is_skip("SKIP is not a report")


@pytest.mark.asyncio
async def test_write_report_retries_skip_sentinel() -> None:
    ctx = {
        "sources": ["macro_intel"],
        "prices": {"BTC": {"usd": 81000.0}},
        "macro_intel": [],
        "macro_stories": [],
    }
    with patch(
        "rugmunchbot.llm_config.chat_with_fallback",
        AsyncMock(side_effect=["SKIP", "x " * 900]),
    ) as llm:
        out = await write_report(ctx)
    assert llm.await_count == 2
    assert len(out) > 1200


def test_prompt_forbids_skip_when_context_has_no_links() -> None:
    ctx = {"generated_at": "2026-10-17T12:00:00+00:00", "prices": {}, "macro_intel": []}
    assert count_context_links(ctx) == 0
    user = build_messages(ctx)[1]["content"].lower()
    assert "do not return skip" in user
