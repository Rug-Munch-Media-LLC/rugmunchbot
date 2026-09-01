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
    assert "bridge.jpg" in post.image_url  # image attached
    assert "/api/v1/media/thumb?url=" in post.image_url
    assert "Why it matters" in str(post)  # commentary present
    assert "rugmunch.io/news" in str(post)  # site link present


def test_dislocation_and_social_spikes_route_to_alpha(monkeypatch):
    """2026-08-29 doctrine: funding dislocation + social spikes are ALPHA
    content (CONTENT-STRATEGY-CANONICAL), never @rmialerts."""
    from rugmunchbot.channels.scheduler import _register as reg

    monkeypatch.setattr(
        reg, "_chat_id", lambda ch: {"alpha": "-100alpha", "alerts": "-100alerts"}.get(ch)
    )
    monkeypatch.setattr(reg, "CHANNEL_JOBS_ENABLED", True)
    monkeypatch.setattr(reg, "ALERTS_CHANNEL_ENABLED", True)
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
    names = {j["name"] for j in app.job_queue.repeating}

    dislo = next((j for j in app.job_queue.repeating if j["name"] == "channel_dislocation"), None)
    assert dislo is not None, "dislocation job must register when alpha enabled"
    import inspect

    source = inspect.getsource(dislo["callback"])
    assert '_chat_id("alpha")' in source
    assert '_chat_id("alerts")' not in source

    social = next(
        (j for j in app.job_queue.repeating if j["name"] == "channel_social_spikes"), None
    )
    assert social is not None, "social spikes job must register when alpha enabled"
    source = inspect.getsource(social["callback"])
    assert '_chat_id("alpha")' in source
    assert '_chat_id("alerts")' not in source

    # The alerts channel digest job still registers (scanner risk digest).
    assert "channel_alerts" in names


def test_alerts_digest_excludes_whale_event_types(monkeypatch):
    """The alerts digest rail (fetch_risk_alerts) admits imminent-rug event
    types only — whale_move / token_transfer / transfer never reach it."""
    import rugmunchbot.channels.content as content
    from rugmunchbot.channels.content import ALERT_EVENT_TYPES, WHALE_EVENT_TYPES

    assert not (ALERT_EVENT_TYPES & WHALE_EVENT_TYPES)

    entries = [
        {
            "type": "whale_move_webhook",
            "event_type": "whale_move",
            "token": "WALE",
            "token_symbol": "WALE",
            "chain": "solana",
            "severity": "high",
            "title": "Whale moved",
            "attribution": {},
        },
        {
            "type": "deployer_dump_webhook",
            "event_type": "deployer_dump",
            "token": "RUGG",
            "token_symbol": "RUGG",
            "chain": "solana",
            "severity": "critical",
            "title": "Deployer dumping",
            "attribution": {},
        },
    ]

    async def _fake_get_recent_alerts(limit, severity=""):
        return entries

    import rugmunchbot._vendor.alert_pipeline as vendor

    monkeypatch.setattr(vendor, "get_recent_alerts", _fake_get_recent_alerts)
    # The webhook fetch is invoked through content._fetch_pushed_webhook_alerts
    # which imports get_recent_alerts at call time — patch the module directly.
    import asyncio

    items = asyncio.run(content.fetch_risk_alerts(limit=5))
    # Whale event must NOT appear; the deployer_dump event MAY (scanner rail
    # quiet → only the webhook rail contributes, and deployer_dump passes).
    whales = [i for i in items if i.get("symbol") == "WALE"]
    assert not whales, "whale_move must never reach the alerts digest"


def test_scan_churn_drops_candidate_when_scan_fails():
    """A candidate whose full scan fails is DROPPED, never posted from sketch
    flags alone — a sketch-only "unscanned · thin_liquidity" card carries no
    why (scans-of-interest rule: either run the deep scan so we HAVE data,
    or drop it)."""
    import asyncio

    from rugmunchbot.channels import content

    async def _failed_scan(address, chain):
        return None

    async def _timeout_scan(address, chain):
        raise TimeoutError("backend slow")

    import rugmunchbot.tools.scanner._scans as scanner_module

    original = scanner_module.scan_token_v2
    try:
        scanner_module.scan_token_v2 = _failed_scan
        n_fail = asyncio.run(
            content._churn_one_scan(
                {
                    "address": "SketchAddr11111111111111111111111111111111",
                    "symbol": "SUS",
                    "name": "Sus Token",
                    "chain": "solana",
                    "risk_flags": ["thin_liquidity", "fresh_launch", "unscanned"],
                    "sketch_score": 59.6,
                }
            )
        )
        scanner_module.scan_token_v2 = _timeout_scan
        n_timeout = asyncio.run(
            content._churn_one_scan(
                {
                    "address": "SketchAddr22222222222222222222222222222222",
                    "symbol": "SUS2",
                    "chain": "solana",
                    "risk_flags": ["thin_liquidity"],
                    "sketch_score": 61.0,
                }
            )
        )
    finally:
        scanner_module.scan_token_v2 = original
    assert n_fail is None
    assert n_timeout is None


def test_scan_churn_notes_are_flagged_churned(monkeypatch):
    """fetch_suspect_scan_notes returns notes marked churned=True."""
    from rugmunchbot.channels import content

    async def _fake_churn_one(candidate):
        return {"address": "X", "symbol": "S", "churned": True, "risk_flags": ["x"]}

    async def _fake_trend(chain="all", limit=4, rank="risk"):
        from rugmunchbot.tools.registry import ToolResult

        return ToolResult(
            tool="trend_by_chain",
            ok=True,
            data={
                "tokens": [{"address": "X", "symbol": "S", "chain": "solana", "risk_flags": ["x"]}]
            },
        )

    monkeypatch.setattr(content, "_churn_one_scan", _fake_churn_one)
    # fetch_suspect_scan_notes imports trend_by_chain locally from
    # rugmunchbot.tools.trend_algo — patch it at the module it imports from.
    import rugmunchbot.tools.trend_algo as trend_algo

    monkeypatch.setattr(trend_algo, "trend_by_chain", _fake_trend)
    import asyncio

    notes = asyncio.run(content.fetch_suspect_scan_notes(limit=2))
    assert notes, "suspect scan notes should be returned"
    assert notes[0]["churned"] is True
