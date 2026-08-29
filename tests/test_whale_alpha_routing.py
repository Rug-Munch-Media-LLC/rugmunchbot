"""Channel routing overhaul — whale alerts → alpha, quality job wiring.

Deliverables 1+2 of the 2026-08-29 quality overhaul:

* Whale-alert slop is REMOVED from @rmialerts: the whale tick resolves the
  ALPHA channel and posts the 🐋 WHALE WATCH digest there (the alerts
  channel keeps exploit/scam/rug + shill-campaign detections only).
* The alpha lane owns whale moves + smart money + launches + KOL trades.
* The daily-quality job registers on main when enabled.
"""

from __future__ import annotations

from typing import Any


class _JQ:
    def __init__(self):
        self.repeating: list[dict[str, Any]] = []
        self.once: list[dict[str, Any]] = []

    def run_repeating(self, callback, interval, first=None, name=None, **kw):
        self.repeating.append({"callback": callback, "name": name, "interval": interval})

    def run_once(self, callback, when, name=None, **kw):
        self.once.append({"name": name})

    def run_daily(self, callback, time, days=None, name=None, **kw):
        pass


class _App:
    def __init__(self):
        self.job_queue = _JQ()


def test_whale_job_registers_when_alpha_enabled(monkeypatch):
    """The whale tick lives on the ALPHA channel now, not alerts."""
    from rugmunchbot.channels.scheduler import _register as reg

    monkeypatch.setattr(reg, "_chat_id", lambda ch: "-100alpha" if ch == "alpha" else None)
    monkeypatch.setattr(reg, "CHANNEL_JOBS_ENABLED", True)
    monkeypatch.setattr(reg, "ALERTS_CHANNEL_ENABLED", True)  # alerts enabled too
    monkeypatch.setattr(reg, "ALPHA_CHANNEL_ENABLED", True)
    monkeypatch.setattr(reg, "SCAN_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "TG_NEWS_ENABLED", False)
    monkeypatch.setattr(reg, "SCAM_WATCH_ENABLED", False)
    monkeypatch.setattr(reg, "PERSONAL_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "DAILY_MARKET_SUMMARY_ENABLED", False)
    monkeypatch.setattr(reg, "UPDATES_CHANNEL_ENABLED", False)
    monkeypatch.delenv("RMI_RAG_POSTS_ENABLED", raising=False)
    monkeypatch.delenv("RMI_DAILY_QUALITY_ENABLED", raising=False)
    monkeypatch.delenv("RMI_SHILL_WATCH_ENABLED", raising=False)

    app = _App()
    reg.register_channel_jobs(app)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_whale_alerts" in names

    # The whale tick's destination is the ALPHA channel (registration gate
    # resolves it via reg._chat_id — already asserted by registration).


def test_alpha_content_includes_kol_trades(monkeypatch):
    """fetch_alpha_content gains a kol_trades lane (fail-open)."""
    from rugmunchbot.channels import content

    async def _no_kol(limit):
        return []

    async def _no_rows(chain, limit):
        return []

    monkeypatch.setattr(content, "_fetch_kol_trades", _no_kol)
    monkeypatch.setattr(content, "_fetch_whale_moves", _no_rows)
    monkeypatch.setattr(content, "_fetch_early_entries", _no_rows)
    monkeypatch.setattr(content, "_fetch_smart_money", _no_rows)
    monkeypatch.setattr(content, "_fetch_launches", _no_rows)

    import asyncio

    result = asyncio.run(content.fetch_alpha_content())
    assert "kol_trades" in result


def test_alpha_formatter_accepts_kol_trades():
    from rugmunchbot.channels.formatters import format_alpha_post

    post = format_alpha_post(
        [], [], [], kol_trades=[{"title": "KOL buys $XYZ", "url": "https://x.com/a"}]
    )
    assert "KOL Trades" in str(post)
    assert "https://x.com/a" in str(post)
    # Section omitted when no kol trades.
    post2 = format_alpha_post([], [], [])
    assert "KOL Trades" not in str(post2)


def test_shill_watch_job_registers_on_alerts(monkeypatch):
    from rugmunchbot.channels.scheduler import _register as reg

    monkeypatch.setattr(reg, "_chat_id", lambda ch: "-100alerts" if ch == "alerts" else None)
    monkeypatch.setattr(reg, "CHANNEL_JOBS_ENABLED", True)
    monkeypatch.setattr(reg, "ALERTS_CHANNEL_ENABLED", True)
    monkeypatch.setattr(reg, "ALPHA_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "SCAN_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "TG_NEWS_ENABLED", False)
    monkeypatch.setattr(reg, "SCAM_WATCH_ENABLED", False)
    monkeypatch.setattr(reg, "PERSONAL_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "DAILY_MARKET_SUMMARY_ENABLED", False)
    monkeypatch.setattr(reg, "UPDATES_CHANNEL_ENABLED", False)
    monkeypatch.setenv("RMI_SHILL_WATCH_ENABLED", "true")

    app = _App()
    reg.register_channel_jobs(app)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_shill_watch" in names


def test_daily_quality_job_registers_on_main(monkeypatch):
    from rugmunchbot.channels.scheduler import _register as reg

    monkeypatch.setattr(reg, "_chat_id", lambda ch: "-100main" if ch == "main" else None)
    monkeypatch.setattr(reg, "CHANNEL_JOBS_ENABLED", True)
    monkeypatch.setattr(reg, "ALERTS_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "ALPHA_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "SCAN_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "TG_NEWS_ENABLED", False)
    monkeypatch.setattr(reg, "SCAM_WATCH_ENABLED", False)
    monkeypatch.setattr(reg, "PERSONAL_CHANNEL_ENABLED", False)
    monkeypatch.setattr(reg, "DAILY_MARKET_SUMMARY_ENABLED", False)
    monkeypatch.setattr(reg, "UPDATES_CHANNEL_ENABLED", False)
    monkeypatch.setenv("RMI_DAILY_QUALITY_ENABLED", "true")

    app = _App()
    reg.register_channel_jobs(app)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_daily_quality" in names


def test_site_link_helpers():
    """Site-link contract: every channel post carries the exact site link."""
    from rugmunchbot.channels.formatters._primitives import (
        enforce_site_link,
        feed_site_line,
        has_site_link,
        site_news_line,
    )

    assert "rugmunch.io/news" in site_news_line()
    assert "feed.rugmunch.io" in feed_site_line("abc123")
    assert "feed.rugmunch.io/p/abc123" in feed_site_line("abc123")

    text = "Some post text"
    linked = enforce_site_link(text, site="news")
    assert "rugmunch.io/news" in linked
    # Idempotent: already-linked text is unchanged.
    assert enforce_site_link(linked, site="news") == linked
    assert has_site_link(linked)


def test_main_post_quality_contract():
    """Main posts: image (when available) + commentary + site link."""
    from rugmunchbot.channels.formatters import format_feature_post

    post = format_feature_post(
        {
            "title": "Bridge exploit drains $190M",
            "url": "https://rekt.news/bridge-190m",
            "source": "rekt.news",
            "summary": "A cross-chain bridge was compromised and roughly $190M "
            "was drained across three chains in under an hour.",
            "commentary": "The largest exploit of the year so far.",
            "image_url": "https://example.com/bridge.jpg",
        }
    )
    assert post.image_url == "https://example.com/bridge.jpg"  # image attached
    assert "Why it matters" in str(post)  # commentary present
    assert "rugmunch.io/news" in str(post)  # site link present
