"""Tests for the Midday Market Report (channels/midday_market_report/).

Covers: the macro facet filter (a crypto-only story is excluded), the
schedule, the prompt (macro framing, not a crypto-story list), the
serializer, the graphic wiring, and the scheduler registration. All
external calls (rugbrain HTTP, Telegram send, LLM, Redis) are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.channels.midday_market_report import (
    EDITION,
    MIDDAY_REPORT_GRAPHIC,
    TASK_CLASS,
    _serialize_context,
    build_messages,
    midday_report_run_time,
    post_midday_report,
)
from rugmunchbot.channels.midday_market_report._fetch import _is_macro_story


def _macro_story(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Fed holds rates steady as CPI cools to 2.9%",
        "summary": "The FOMC left the target range unchanged; CPI printed below consensus.",
        "source": "Reuters",
        "url": "https://reuters.com/markets/fed-cpi",
        "category": "macro",
        "image_url": "https://img.rugmunch.io/fed.png",
    }
    base.update(over)
    return base


def _crypto_only_story(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "New memecoin launches on Solana",
        "summary": "A fresh token deployed with a 90% dev allocation.",
        "source": "CryptoSlate",
        "url": "https://cryptoslate.com/memecoin",
        "category": "defi",
    }
    base.update(over)
    return base


def test_is_macro_story_accepts_macro() -> None:
    assert _is_macro_story(_macro_story()) is True


def test_is_macro_story_rejects_crypto_only() -> None:
    assert _is_macro_story(_crypto_only_story()) is False


def test_edition_name() -> None:
    assert EDITION == "Midday Market Report"


def test_task_class_is_market_brief() -> None:
    assert TASK_CLASS == "market_brief"


def test_run_time_defaults_1230_et() -> None:
    t = midday_report_run_time()
    assert (t.hour, t.minute) == (12, 30)
    assert str(t.tzinfo) == "America/New_York"


def test_prompt_is_macro_framed() -> None:
    msgs = build_messages({"generated_at": "2026-10-17T12:00:00+00:00", "macro_stories": []})
    system = msgs[0]["content"]
    assert "Midday Market Report" in system
    assert "NOT A CRYPTO-STORY LIST" in system
    assert "THE MACRO TAPE" in system
    assert "THE FED & THE DATA" in system
    assert "HOW IT'S LANDING ON CRYPTO" in system


def test_prompt_injects_real_date_no_placeholder() -> None:
    """The masthead date must be the real date, never the literal placeholder."""
    msgs = build_messages({"generated_at": "2026-10-17T12:00:00+00:00", "macro_stories": []})
    system = msgs[0]["content"]
    assert "{Weekday, Month DD, YYYY}" not in system
    assert "Saturday, October 17, 2026" in system


def test_serialize_context_includes_macro_blocks() -> None:
    ctx = {
        "macro_stories": [_macro_story()],
        "macro_intel": [{"content": "DXY up 0.4%", "collection": "market_health"}],
        "prices": {"bitcoin": {"usd": 68000.0, "usd_24h_change": 1.2}},
        "global_metrics": {"total_mcap": 2.4e12, "btc_dominance": 54.3},
    }
    text = _serialize_context(ctx)
    assert "MACRO STORIES" in text
    assert "MACRO INTEL" in text
    assert "CRYPTO TAPE" in text
    assert "GLOBAL CRYPTO METRICS" in text
    assert "bitcoin" in text


def test_serialize_context_empty() -> None:
    assert _serialize_context({}) == "(no data)"


def test_graphic_registered() -> None:
    """The report resolves to a real operator image, never the brand default.

    Regression (2026-10-17): the publish module passed the config FILENAME
    to ``graphic_for`` as if it were a post type, so the report silently
    shipped the generic brand default. The map now holds a real asset and
    the config value is an override resolved via ``graphic_for_type``.
    """
    from rugmunchbot.support.post_graphics import (
        DEFAULT_GRAPHIC,
        graphic_for,
        graphic_for_type,
    )

    resolved = graphic_for("midday_report")
    assert resolved != DEFAULT_GRAPHIC
    assert resolved.endswith((".png", ".jpg", ".jpeg"))
    # The config override resolves to a full brand URL (not the default).
    assert graphic_for_type("midday_report", MIDDAY_REPORT_GRAPHIC) != DEFAULT_GRAPHIC


@pytest.mark.asyncio
async def test_post_midday_report_no_chat_id() -> None:
    with patch("rugmunchbot.channels.scheduler._core._chat_id", return_value=None):
        assert await post_midday_report(MagicMock()) == 0


@pytest.mark.asyncio
async def test_post_midday_report_no_sources() -> None:
    with (
        patch("rugmunchbot.channels.scheduler._core._chat_id", return_value="-100"),
        patch("rugmunchbot.channels.daily_rug._guard.already_posted_today", return_value=False),
        patch("rugmunchbot.channels.daily_rug._guard.claim_today", return_value=True),
        patch("rugmunchbot.channels.daily_rug._guard.release_today") as rel,
        patch(
            "rugmunchbot.channels.midday_market_report._publish.gather_midday_report_data",
            new=AsyncMock(return_value={"sources": []}),
        ),
    ):
        assert await post_midday_report(MagicMock()) == 0
        rel.assert_called_once()


@pytest.mark.asyncio
async def test_post_midday_report_guardrails_block() -> None:
    ctx = {"sources": ["macro_stories"], "macro_stories": [_macro_story()]}
    long_report = (
        "<b>━━━ Midday Market Report ━━━</b>\n"
        + "The Fed held rates. " * 40
        + "\nhttps://a.com https://b.com https://c.com"
    )
    with (
        patch("rugmunchbot.channels.scheduler._core._chat_id", return_value="-100"),
        patch("rugmunchbot.channels.daily_rug._guard.already_posted_today", return_value=False),
        patch("rugmunchbot.channels.daily_rug._guard.claim_today", return_value=True),
        patch("rugmunchbot.channels.daily_rug._guard.release_today") as rel,
        patch(
            "rugmunchbot.channels.midday_market_report._publish.gather_midday_report_data",
            new=AsyncMock(return_value=ctx),
        ),
        patch(
            "rugmunchbot.channels.midday_market_report._publish._write_report",
            new=AsyncMock(return_value=long_report),
        ),
        patch(
            "rugmunchbot.channels.report_guardrails.report_guardrails",
            new=AsyncMock(return_value=(False, ["stale_newest_claim"])),
        ),
    ):
        assert await post_midday_report(MagicMock()) == 0
        rel.assert_called_once()
