"""Scam-call channel ingestion + routing (2026-08-29 content-flow overhaul).

Deliverable 2: the tg_news sweep now pulls scam-call channels (discovered
and the explicit ``SCAM_CALL_CHANNELS`` list), tags their articles
``category="scam"``, and routes the scored digest to the ALERTS channel —
never the news channel. Contract:

* ``route_news`` sends scam-category stories in the news band → ``"alerts"``
  and above the main gate → ``"main"`` (never ``"news"``).
* ``resolve_channel_sources`` includes scam-classified channels in the sweep.
* ``format_scam_digest`` renders the alerts-card shape with a site link.
"""

from __future__ import annotations

import pytest

from rugmunchbot.channels.router import ROUTE_SCAM_CATEGORY, route_news
from rugmunchbot.channels.telegram_news._sources._channels import (
    classify_channel_category,
    resolve_channel_sources,
)
from rugmunchbot.channels.telegram_news._sources._config import SCAM_CALL_CHANNELS


def test_scam_call_channels_configured():
    assert "thepumpalert" in SCAM_CALL_CHANNELS
    assert "CallAnalyser" in SCAM_CALL_CHANNELS


def test_route_news_scam_band():
    """Scam-call content in the news band → alerts, never the news channel."""
    assert route_news(0.1, ROUTE_SCAM_CATEGORY) == "skip"
    assert route_news(0.5, ROUTE_SCAM_CATEGORY) == "alerts"
    assert route_news(0.9, ROUTE_SCAM_CATEGORY) == "main"
    # News-category stories still use the news band as before.
    assert route_news(0.5, "news") == "news"


def test_classify_scam_channels_still_scam():
    """Pump-group / seed-phrase titles classify scam (swept, not dropped)."""
    assert classify_channel_category("PUMP GROUP SIGNALS", "pump_group_x") == "scam"


@pytest.mark.asyncio
async def test_resolve_sources_sweeps_scam_channels(monkeypatch):
    """Scam-classified channels appear in the sweep sources, tagged scam."""
    from rugmunchbot.channels import telegram_news as tg

    async def _fake_discover(client):
        # Same (username, title) shape the real discovery returns.
        return [
            ("CallAnalyser", "KOL Call Analysis"),
            ("cointelegraph", "CoinTelegraph"),
            ("SomePumpGroup", "PUMP SIGNALS"),
            ("myprivatechat", "My Private Chat"),
        ]

    # _channels.py binds discover_followed_channels at module scope — patch
    # the reference the source resolver actually calls.
    monkeypatch.setattr(
        "rugmunchbot.channels.telegram_news._sources._channels.discover_followed_channels",
        _fake_discover,
    )
    monkeypatch.setattr(tg, "TG_NEWS_AUTO_DISCOVER", True)

    sources = await resolve_channel_sources(client=object())
    by_handle = {s["username"].lower(): s for s in sources}
    # Scam channels are swept (not dropped) and tagged scam.
    assert "somepumpgroup" in by_handle
    assert by_handle["somepumpgroup"]["category"] == "scam"
    assert by_handle["somepumpgroup"]["channel_category"] == "scam"
    # News + alpha still swept.
    assert by_handle["cointelegraph"]["category"] == "crypto_news"
    # Personal chats remain excluded.
    assert "myprivatechat" not in by_handle


@pytest.mark.asyncio
async def test_channel_sources_tags_explicit_scam_handles(monkeypatch):
    """The explicit fallback list tags SCAM_CALL_CHANNELS as category scam."""
    from rugmunchbot.channels import telegram_news as tg

    monkeypatch.setattr(tg, "TG_NEWS_CHANNELS", ["thepumpalert", "cointelegraph", "lookonchain"])
    sources = await resolve_channel_sources()  # client None → explicit list
    by_handle = {s["username"].lower(): s for s in sources}
    assert by_handle["thepumpalert"]["category"] == "scam"
    assert by_handle["thepumpalert"]["channel_category"] == "scam"
    assert by_handle["cointelegraph"]["category"] == "crypto_news"


def test_format_scam_digest_renders_and_site_links():
    from rugmunchbot.channels.formatters import format_scam_digest

    post = format_scam_digest(
        [
            {
                "title": "Rugged: $FAKE called by 3 channels",
                "url": "https://t.me/thepumpalert/123",
                "source": "thepumpalert",
                "summary": "Coordinated calls pushed $FAKE hours before the dump.",
            }
        ]
    )
    text = str(post)
    assert "Scam Calls" in text
    assert "t.me/thepumpalert/123" in text
    assert "rugmunch.io/news" in text  # site-link contract on channel posts


def test_route_chat_id_accepts_alerts(monkeypatch):
    """``_route_chat_id`` resolves the alerts destination like the others."""
    from rugmunchbot.channels.scheduler._core import _route_chat_id

    monkeypatch.setattr(
        "rugmunchbot.channels.scheduler._core._chat_id",
        lambda ch: f"-100{ch}" if ch in ("news", "main", "alpha", "alerts") else None,
    )
    assert _route_chat_id("alerts") == "-100alerts"
    assert _route_chat_id("main") == "-100main"
    assert _route_chat_id("skip") is None
    assert _route_chat_id("bogus") is None
