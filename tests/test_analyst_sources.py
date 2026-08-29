"""LookOnChain / analyst source wiring (2026-08-29 quality overhaul #5).

Contract: analyst/on-chain channels (LookOnChain & co.) are ALWAYS swept
(never bucketed ``other`` by the title heuristic), tagged
``channel_category="analyst"``, and their high-importance stories route to
MAIN (source-linked) + the news page — the existing news pipeline handles
both once the source is swept.
"""

from __future__ import annotations

import pytest

from rugmunchbot.channels.router import route_news
from rugmunchbot.channels.telegram_news._sources._channels import (
    classify_channel_category,
    resolve_channel_sources,
)
from rugmunchbot.channels.telegram_news._sources._config import ANALYST_CHANNELS


def test_analyst_channels_are_configured():
    assert "lookonchain" in ANALYST_CHANNELS
    assert "spotonchain" in ANALYST_CHANNELS
    assert "ArkhamIntel" in ANALYST_CHANNELS


def test_lookonchain_never_classified_other():
    """The old bug: 'lookonchain' hit no news/alpha keyword → 'other' →
    auto-discovery silently skipped it. Now: analyst handles are news."""
    assert classify_channel_category("Lookonchain", "lookonchain") == "news"
    assert classify_channel_category("Arkham Intel", "ArkhamIntel") == "news"
    # A generic personal channel is still excluded.
    assert classify_channel_category("My Private Chats", "some_personal_handle") == "other"


@pytest.mark.asyncio
async def test_channel_sources_tag_analyst():
    sources = {s["username"]: s for s in await resolve_channel_sources()}
    look = sources.get("lookonchain")
    assert look is not None
    assert look["category"] == "crypto_news"  # swept as news
    assert look["channel_category"] == "analyst"  # tagged for routing


def test_analyst_story_routes_to_main_at_high_importance():
    """Analyst content that clears the main gate posts to @cryptorugmuncher."""
    assert route_news(0.82, "news") == "main"
    # Below the main gate it still reaches the news channel (news page).
    assert route_news(0.5, "news") == "news"


def test_main_always_categories_include_analyst_friendly_escalations():
    from rugmunchbot.channels.router import ROUTE_MAIN_ALWAYS_CATEGORIES

    # Exploit/rug categories still force main regardless of score.
    assert "exploit" in ROUTE_MAIN_ALWAYS_CATEGORIES
