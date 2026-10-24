"""Channel routing overhaul — whale alerts → alpha, quality job wiring.

Deliverables 1+2 of the 2026-08-29 quality overhaul:

* Whale-alert slop is REMOVED from @rmialerts: the whale tick resolves the
  ALPHA channel and posts the 🐋 WHALE WATCH digest there (the alerts
  channel keeps exploit/scam/rug + shill-campaign detections only).
* The alpha lane owns whale moves + smart money + launches + KOL trades.
* The daily-quality job registers on main when enabled.
"""

from __future__ import annotations

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


def test_site_link_helpers():
    """Site-link contract: every channel post carries the exact site link."""
    from rugmunchbot.channels.formatters._primitives import (
        enforce_site_link,
        feed_site_line,
        has_site_link,
        site_news_line,
    )

    assert "rugmunch.io/news" in site_news_line()
    assert "rugmunch.io/story/abc123" in feed_site_line("abc123")
    assert "rugmunch.io/story/abc123" in feed_site_line("abc123")

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
