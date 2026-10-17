"""Tests for the Evening Wrap (channels/evening_wrap/).

Covers: the recap facet (scams/shills/rugs), the schedule, the prompt
(recap framing, not a market brief), the serializer, the graphic wiring,
and the scheduler registration. All external calls are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.channels.evening_wrap import (
    EDITION,
    EVENING_WRAP_GRAPHIC,
    TASK_CLASS,
    _serialize_context,
    build_messages,
    evening_wrap_run_time,
    post_evening_wrap,
)


def _alert(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "RUGTOKEN",
        "symbol": "RUG",
        "chain": "base",
        "severity": "critical",
        "risk_score": 95,
        "risk_flags": ["honeypot", "LP pulled"],
        "url": "https://rugmunch.io/scan/RUG",
    }
    base.update(over)
    return base


def _campaign(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "token": "SHILLCOIN",
        "kol_count": 7,
        "kols": ["@alpha1", "@alpha2"],
        "url": "https://rugmunch.io/shill/SHILLCOIN",
    }
    base.update(over)
    return base


def test_edition_name() -> None:
    assert EDITION == "Evening Wrap"


def test_task_class_is_recap() -> None:
    assert TASK_CLASS == "recap"


def test_run_time_defaults_2000_et() -> None:
    t = evening_wrap_run_time()
    assert (t.hour, t.minute) == (20, 0)
    assert str(t.tzinfo) == "America/New_York"


def test_prompt_is_recap_framed() -> None:
    msgs = build_messages({"generated_at": "2026-10-17T20:00:00+00:00", "alerts": []})
    system = msgs[0]["content"]
    assert "Evening Wrap" in system
    assert "NOT A MARKET BRIEF" in system
    assert "WHAT RUGGED TODAY" in system
    assert "THE SHILL DESK" in system
    assert "THE DAY'S PATTERN" in system


def test_prompt_injects_real_date_no_placeholder() -> None:
    """The masthead date must be the real date, never the literal placeholder."""
    msgs = build_messages({"generated_at": "2026-10-17T20:00:00+00:00", "alerts": []})
    system = msgs[0]["content"]
    assert "{Weekday, Month DD, YYYY}" not in system
    assert "Saturday, October 17, 2026" in system


def test_serialize_context_includes_recap_blocks() -> None:
    ctx = {
        "alerts": [_alert()],
        "campaigns": [_campaign()],
        "recap_intel": [
            {"content": "Deployer dumped 40% of supply", "collection": "threat_intel_v2"}
        ],
    }
    text = _serialize_context(ctx)
    assert "SCANNER ALERTS" in text
    assert "SHILL CAMPAIGNS" in text
    assert "RECAP INTEL" in text
    assert "RUGTOKEN" in text
    assert "SHILLCOIN" in text


def test_serialize_context_empty() -> None:
    assert _serialize_context({}) == "(no data)"


def test_graphic_registered() -> None:
    from rugmunchbot.support.post_graphics import POST_TYPE_GRAPHICS

    assert POST_TYPE_GRAPHICS.get("evening_wrap") == EVENING_WRAP_GRAPHIC


@pytest.mark.asyncio
async def test_post_evening_wrap_no_chat_id() -> None:
    with patch("rugmunchbot.channels.scheduler._core._chat_id", return_value=None):
        assert await post_evening_wrap(MagicMock()) == 0


@pytest.mark.asyncio
async def test_post_evening_wrap_no_sources() -> None:
    with (
        patch("rugmunchbot.channels.scheduler._core._chat_id", return_value="-100"),
        patch("rugmunchbot.channels.daily_rug._guard.already_posted_today", return_value=False),
        patch("rugmunchbot.channels.daily_rug._guard.claim_today", return_value=True),
        patch("rugmunchbot.channels.daily_rug._guard.release_today") as rel,
        patch(
            "rugmunchbot.channels.evening_wrap._publish.gather_evening_wrap_data",
            new=AsyncMock(return_value={"sources": []}),
        ),
    ):
        assert await post_evening_wrap(MagicMock()) == 0
        rel.assert_called_once()


@pytest.mark.asyncio
async def test_post_evening_wrap_guardrails_block() -> None:
    ctx = {"sources": ["alerts"], "alerts": [_alert()]}
    long_wrap = (
        "<b>━━━ Evening Wrap ━━━</b>\n"
        + "Another rug pulled today. " * 40
        + "\nhttps://a.com https://b.com https://c.com"
    )
    with (
        patch("rugmunchbot.channels.scheduler._core._chat_id", return_value="-100"),
        patch("rugmunchbot.channels.daily_rug._guard.already_posted_today", return_value=False),
        patch("rugmunchbot.channels.daily_rug._guard.claim_today", return_value=True),
        patch("rugmunchbot.channels.daily_rug._guard.release_today") as rel,
        patch(
            "rugmunchbot.channels.evening_wrap._publish.gather_evening_wrap_data",
            new=AsyncMock(return_value=ctx),
        ),
        patch(
            "rugmunchbot.channels.evening_wrap._publish._write_wrap",
            new=AsyncMock(return_value=long_wrap),
        ),
        patch(
            "rugmunchbot.channels.report_guardrails.report_guardrails",
            new=AsyncMock(return_value=(False, ["dead_links=1"])),
        ),
    ):
        assert await post_evening_wrap(MagicMock()) == 0
        rel.assert_called_once()
