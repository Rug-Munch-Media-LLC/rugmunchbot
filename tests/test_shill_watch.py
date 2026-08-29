"""Shill-campaign detection — coordinated-pusher contract (2026-08-29).

Covers deliverable 4 of the channel-quality overhaul:

* Pure detection — same token (address or ``$TICKER``) pushed by >= N
  distinct channels inside a window is a campaign; a single loud channel is
  coverage, not coordination.
* Formatting — the alert card lists the pushers and earliest pushes with
  links; the alert lands on the ALERTS channel (high-signal doctrine).
* Dedup — per token per day; failed send releases the mark (retryable).

Detection is pure/deterministic (offline); the runner's I/O is patched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugmunchbot._vendor.news_ingest.models import NewsArticle
from rugmunchbot.channels.shill_watch import (
    SHILL_MIN_ACCOUNTS,
    SHILL_MIN_MENTIONS,
    ShillCampaign,
    detect_shill_campaigns,
    format_shill_alert,
)


def _article(channel: str, title: str, entities: list[str], hours_ago: float = 1.0) -> NewsArticle:
    return NewsArticle(
        title=title,
        body=f"{title} — call from {channel}",
        source_channel=channel,
        source_url=f"https://t.me/{channel}/1",
        category="alpha",
        entities=entities,
        posted_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


def test_detect_flags_coordinated_campaign():
    """Same address pushed by 3 distinct channels = campaign."""
    addr = "0x" + "a" * 40
    articles = [
        _article("calls_one", "PUMP $MOON NOW", [addr]),
        _article("alpha_king", "MOON gem of the day", [addr]),
        _article("whale_whisper", "Get in early on MOON", [addr]),
    ]
    campaigns = detect_shill_campaigns(articles, min_accounts=3, min_mentions=3)
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c.chain == "ethereum"
    assert len(c.accounts) == 3
    assert c.score == 3 * 10 + 3


def test_detect_ignores_single_loud_channel():
    """One channel pushing hard is coverage, not coordination."""
    addr = "0x" + "b" * 40
    articles = [_article("only_one", "MOON x10", [addr]) for _ in range(6)]
    campaigns = detect_shill_campaigns(articles, min_accounts=3, min_mentions=3)
    assert campaigns == []


def test_detect_ticker_campaign_and_bluechip_exclusion():
    """$TICKER coordination detected; blue-chip tickers are never shill."""
    articles = [
        _article("calls_one", "MEME to the moon", ["$MEME"]),
        _article("alpha_king", "MEME gem", ["$MEME"]),
        _article("whale_whisper", "MEME early", ["$MEME"]),
        # BTC is mentioned everywhere by everyone — never "coordinated shill".
        _article("btc_fan", "BTC to 200k", ["$BTC"]),
        _article("btc_fan2", "BTC accumulation", ["$BTC"]),
        _article("btc_fan3", "BTC breakout", ["$BTC"]),
    ]
    campaigns = detect_shill_campaigns(articles, min_accounts=3, min_mentions=3)
    assert len(campaigns) == 1
    assert campaigns[0].token == "$MEME"
    assert campaigns[0].chain == "ticker"


def test_detect_window_excludes_old_mentions():
    addr = "0x" + "c" * 40
    articles = [
        _article("calls_one", "MOON now", [addr], hours_ago=30),
        _article("alpha_king", "MOON gem", [addr], hours_ago=30),
        _article("whale_whisper", "MOON early", [addr], hours_ago=1),
    ]
    campaigns = detect_shill_campaigns(articles, window_hours=24, min_accounts=3, min_mentions=3)
    assert campaigns == []  # only 1 account inside the window


def test_detect_dict_rows_fail_open():
    """Dict-shaped articles (tests/synthetic) also work."""
    articles = [
        {
            "title": "PUMP $PEPE",
            "source_channel": "ch_a",
            "source_url": "https://t.me/ch_a/1",
            "entities": ["$PEPE"],
            "posted_at": datetime.now(UTC).isoformat(),
        },
        {
            "title": "PEPE gem",
            "source_channel": "ch_b",
            "source_url": "https://t.me/ch_b/1",
            "entities": ["$PEPE"],
            "posted_at": datetime.now(UTC).isoformat(),
        },
        {
            "title": "PEPE early",
            "source_channel": "ch_c",
            "source_url": "https://t.me/ch_c/1",
            "entities": ["$PEPE"],
            "posted_at": datetime.now(UTC).isoformat(),
        },
    ]
    campaigns = detect_shill_campaigns(articles, min_accounts=3, min_mentions=3)
    assert len(campaigns) == 1
    assert campaigns[0].token == "$PEPE"


def test_format_alert_lists_pushers_and_links():
    campaign = ShillCampaign(
        token="$MOON",
        chain="ticker",
        accounts=["calls_one", "alpha_king", "whale_whisper"],
        mentions=[
            {"channel": "calls_one", "title": "PUMP MOON", "url": "https://t.me/calls_one/1"},
            {"channel": "alpha_king", "title": "MOON gem", "url": "https://t.me/alpha_king/1"},
        ],
        window_start=datetime.now(UTC).isoformat(),
        window_end=datetime.now(UTC).isoformat(),
        score=33,
    )
    text = format_shill_alert(campaign)
    assert "Coordinated shill: $MOON" in text
    assert "3 distinct accounts" in text
    assert "calls_one" in text
    assert "https://t.me/calls_one/1" in text
    assert "/scan" in text


def test_detect_min_thresholds_respected():
    addr = "0x" + "d" * 40
    articles = [
        _article("calls_one", "MOON", [addr]),
        _article("alpha_king", "MOON", [addr]),
        # 2 accounts, 2 mentions — below the 3/3 threshold.
    ]
    assert detect_shill_campaigns(articles, min_accounts=3, min_mentions=3) == []
    assert len(detect_shill_campaigns(articles, min_accounts=2, min_mentions=2)) == 1


@pytest.mark.asyncio
async def test_run_posts_to_alerts_and_dedupes(monkeypatch):
    """Runner: sweeps → detects → posts to alerts; per-day dedupe; release on fail."""
    from rugmunchbot.channels import shill_watch
    from rugmunchbot.channels.scheduler import _core

    monkeypatch.setattr(_core, "_posted_keys", {})

    addr = "0x" + "e" * 40
    articles = [
        _article("calls_one", "PUMP NOW", [addr]),
        _article("alpha_king", "GEM", [addr]),
        _article("whale_whisper", "EARLY", [addr]),
    ]

    async def _fake_sweep(client=None, lookback_hours=None):
        return articles

    sent: list[tuple[str, str]] = []

    async def _fake_send(bot, chat_id, text):
        sent.append((chat_id, text))
        return True

    class _Ctx:
        class _Bot:
            pass

        bot = _Bot()

    monkeypatch.setattr(shill_watch, "_sweep_shill_sources", _fake_sweep)
    # run_shill_watch imports _chat_id/_safe_send from scheduler._core at
    # call time — patch the core module, not the shill_watch namespace.
    monkeypatch.setattr(_core, "_chat_id", lambda ch: "-100alerts" if ch == "alerts" else None)
    monkeypatch.setattr(_core, "_safe_send", _fake_send)

    posted = await shill_watch.run_shill_watch(_Ctx())
    assert posted == 1
    assert len(sent) == 1
    assert sent[0][0] == "-100alerts"
    assert "Coordinated shill" in sent[0][1]

    # Same day, second run → deduped (no re-alert).
    posted = await shill_watch.run_shill_watch(_Ctx())
    assert posted == 0
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_run_releases_mark_on_failed_send(monkeypatch):
    from rugmunchbot.channels import shill_watch
    from rugmunchbot.channels.scheduler import _core

    monkeypatch.setattr(_core, "_posted_keys", {})

    addr = "0x" + "f" * 40
    articles = [
        _article("calls_one", "PUMP NOW", [addr]),
        _article("alpha_king", "GEM", [addr]),
        _article("whale_whisper", "EARLY", [addr]),
    ]

    async def _fake_sweep(client=None, lookback_hours=None):
        return articles

    fails: list[int] = [0]

    async def _fake_send(bot, chat_id, text):
        fails[0] += 1
        return fails[0] > 1  # first fails, second succeeds

    class _Ctx:
        class _Bot:
            pass

        bot = _Bot()

    monkeypatch.setattr(shill_watch, "_sweep_shill_sources", _fake_sweep)
    monkeypatch.setattr(_core, "_chat_id", lambda ch: "-100alerts" if ch == "alerts" else None)
    monkeypatch.setattr(_core, "_safe_send", _fake_send)

    assert await shill_watch.run_shill_watch(_Ctx()) == 0  # send failed
    assert await shill_watch.run_shill_watch(_Ctx()) == 1  # retry delivers


def test_constants_are_sane():
    assert SHILL_MIN_ACCOUNTS >= 3  # coordination requires >= 3 distinct pushers
    assert SHILL_MIN_MENTIONS >= 3
