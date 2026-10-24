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
    assert "3 accounts in" in text
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


def _stub_runner(monkeypatch, *, importance=0.6):
    """Baseline shill runner env: dedupe store, fake scorer, capture/scan off.

    Four runner tests used to repeat this six-line setup by hand; one seam
    keeps them honest about what the runner actually needs.
    """
    from rugmunchbot.channels.scheduler import _core

    async def _fake_score(campaign):
        campaign.importance = importance
        return importance

    monkeypatch.setattr(_core, "_posted_keys", {})
    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._runner._score_shill_campaign", _fake_score
    )
    monkeypatch.setattr("rugmunchbot.channels.shill_watch._runner.SHILL_SCREENSHOT_ENABLED", False)
    monkeypatch.setattr("rugmunchbot.channels.shill_watch._runner.TG_CAPTURE_ENABLED", False)
    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._scoring.SHILL_SCANNER_EVIDENCE_ENABLED", False
    )


@pytest.mark.asyncio
async def test_run_posts_to_alerts_and_dedupes(monkeypatch):
    """Runner: sweeps → detects → posts to alerts; per-day dedupe; release on fail."""
    from rugmunchbot.channels import shill_watch
    from rugmunchbot.channels.scheduler import _core

    _stub_runner(monkeypatch, importance=0.6)

    addr = "0x" + "e" * 40
    articles = [
        _article("calls_one", "PUMP NOW", [addr]),
        _article("alpha_king", "GEM", [addr]),
        _article("whale_whisper", "EARLY", [addr]),
    ]

    async def _fake_sweep(client=None, lookback_hours=None):
        return articles

    sent: list[tuple[str, str]] = []

    async def _fake_send(bot, chat_id, text, **kw):
        sent.append((chat_id, text))
        return True

    class _Ctx:
        class _Bot:
            pass

        bot = _Bot()

    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._runner._sweep_shill_sources", _fake_sweep
    )
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

    _stub_runner(monkeypatch, importance=0.6)

    addr = "0x" + "f" * 40
    articles = [
        _article("calls_one", "PUMP NOW", [addr]),
        _article("alpha_king", "GEM", [addr]),
        _article("whale_whisper", "EARLY", [addr]),
    ]

    async def _fake_sweep(client=None, lookback_hours=None):
        return articles

    fails: list[int] = [0]

    async def _fake_send(bot, chat_id, text, **kw):
        fails[0] += 1
        return fails[0] > 1  # first fails, second succeeds

    class _Ctx:
        class _Bot:
            pass

        bot = _Bot()

    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._runner._sweep_shill_sources", _fake_sweep
    )
    monkeypatch.setattr(_core, "_chat_id", lambda ch: "-100alerts" if ch == "alerts" else None)
    monkeypatch.setattr(_core, "_safe_send", _fake_send)

    assert await shill_watch.run_shill_watch(_Ctx()) == 0  # send failed
    assert await shill_watch.run_shill_watch(_Ctx()) == 1  # retry delivers


def test_constants_are_sane():
    assert SHILL_MIN_ACCOUNTS >= 3  # coordination requires >= 3 distinct pushers
    assert SHILL_MIN_MENTIONS >= 3


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-30 — intelligent routing + screenshot evidence
# ══════════════════════════════════════════════════════════════════════════


def test_x_status_url_parses():
    """X/Twitter status URLs (incl /i/status/) normalize to one canonical form."""
    from rugmunchbot.channels.shill_watch import _x_status_url

    assert (
        _x_status_url("https://x.com/foo/status/1234567890") == "https://x.com/i/status/1234567890"
    )
    assert _x_status_url("https://twitter.com/foo/status/999") == "https://x.com/i/status/999"
    assert _x_status_url("https://x.com/i/status/42") == "https://x.com/i/status/42"
    assert _x_status_url("https://t.me/CallAnalyser/1") == ""  # Telegram, not X
    assert _x_status_url("") == ""


def test_format_alert_includes_evidence():
    """The alert card explains WHAT/WHO/WHY and attaches screenshot evidence."""
    from rugmunchbot.channels.shill_watch import ShillCampaign, format_shill_alert

    campaign = ShillCampaign(
        token="$MOON",
        chain="ticker",
        accounts=["a_call", "b_alpha"],
        mentions=[{"channel": "a_call", "title": "MOON 100x", "url": "https://x.com/a/status/123"}],
        importance=0.9,
        screenshots=[
            {
                "url": "https://x.com/a/status/123",
                "image_path": "shill_shots/x.png",
                "text": "GET IN $MOON NOW @badkol",
                "account": "a_call",
            }
        ],
    )
    alert = format_shill_alert(campaign)
    assert "Coordinated shill" in alert
    assert "<b>KOL:</b>" in alert
    assert "<b>Why this is likely a scam:</b>" in alert
    assert "GET IN $MOON NOW" in alert


@pytest.mark.asyncio
async def test_run_holds_below_importance_floor(monkeypatch):
    """A campaign scoring below the news floor (0.4) is held — never spammed."""
    from rugmunchbot.channels import shill_watch
    from rugmunchbot.channels.scheduler import _core

    _stub_runner(monkeypatch, importance=0.2)

    addr = "0x" + "d" * 40
    articles = [
        _article("calls_one", "PUMP NOW", [addr]),
        _article("alpha_king", "GEM", [addr]),
        _article("whale_whisper", "EARLY", [addr]),
    ]

    async def _fake_sweep(client=None, lookback_hours=None):
        return articles

    sent: list[tuple[str, str]] = []

    async def _fake_send(bot, chat_id, text, **kw):
        sent.append((chat_id, text))
        return True

    class _Ctx:
        class _Bot:
            pass

        bot = _Bot()

    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._runner._sweep_shill_sources", _fake_sweep
    )
    monkeypatch.setattr(_core, "_chat_id", lambda ch: "-100alerts" if ch == "alerts" else None)
    monkeypatch.setattr(_core, "_safe_send", _fake_send)

    assert await shill_watch.run_shill_watch(_Ctx()) == 0  # below floor → held
    assert len(sent) == 0


@pytest.mark.asyncio
async def test_run_escalates_main_at_gate(monkeypatch):
    """A campaign at/above the main gate posts to BOTH alerts and main."""
    from rugmunchbot.channels import shill_watch
    from rugmunchbot.channels.scheduler import _core

    _stub_runner(monkeypatch, importance=0.8)

    addr = "0x" + "c" * 40
    articles = [
        _article("calls_one", "PUMP NOW", [addr]),
        _article("alpha_king", "GEM", [addr]),
        _article("whale_whisper", "EARLY", [addr]),
    ]

    async def _fake_sweep(client=None, lookback_hours=None):
        return articles

    sent: list[tuple[str, str]] = []

    async def _fake_send(bot, chat_id, text, **kw):
        sent.append((chat_id, text))
        return True

    class _Ctx:
        class _Bot:
            pass

        bot = _Bot()

    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._runner._sweep_shill_sources", _fake_sweep
    )
    monkeypatch.setattr(
        _core,
        "_chat_id",
        lambda ch: "-100main" if ch == "main" else ("-100alerts" if ch == "alerts" else None),
    )
    monkeypatch.setattr(_core, "_safe_send", _fake_send)

    posted = await shill_watch.run_shill_watch(_Ctx())
    assert posted == 1
    assert len(sent) == 2  # alerts + main
    assert {chat for chat, _ in sent} == {"-100alerts", "-100main"}


# ══════════════════════════════════════════════════════════════════════════
# 2026-09-01 — shill→scanner evidence ("why this is likely a scam")
# ══════════════════════════════════════════════════════════════════════════


def test_looks_like_address():
    """Only real chain addresses resolve to a scanner run; tickers do not."""
    from rugmunchbot.channels.shill_watch import _looks_like_address

    assert _looks_like_address("0x" + "a" * 40)
    assert _looks_like_address("11111111111111111111111111111111111111111111")  # solana base58
    assert not _looks_like_address("$MEME")
    assert not _looks_like_address("PEPE")
    assert not _looks_like_address("0x1234")  # too short
    assert not _looks_like_address("")


def test_evidence_bullets_v2_payload():
    """v2 payload shape → defensible on-chain bullets (no fabrication)."""
    from rugmunchbot.channels.shill_watch import _evidence_bullets

    data = {
        "risk_level": "HIGH",
        "safety_score": 22.0,
        "flags": ["Bundled launch", "Owner renounced late"],
        "evidence": {
            "liquidity": {"locked_pct": 8.0},
            "holders": {"top_10_pct": 72.0},
            "funding_analysis": {"fresh_wallet_pct": 65.0, "mixer_funded_pct": 12.0},
            "deployer": {"risk_label": "REPEAT_RUG", "total_deployments": 12, "rug_count": 9},
        },
    }
    bullets = _evidence_bullets(data)
    assert any("HIGH risk" in b for b in bullets)
    assert any("LP only 8% locked" in b for b in bullets)
    assert any("Top 10 holders control 72%" in b for b in bullets)
    assert any("65% of supply held by fresh wallets" in b for b in bullets)
    assert any("Deployer history: REPEAT_RUG" in b for b in bullets)


def test_evidence_bullets_legacy_payload():
    """Legacy scan shape (honeypot/taxes/bundle/lp) → bullets too."""
    from rugmunchbot.channels.shill_watch import _evidence_bullets

    data = {
        "honeypot": True,
        "sell_tax": 30.0,
        "buy_tax": 5.0,
        "bundle_detected": True,
        "lp_locked": False,
        "owner_renounced": False,
        "mintable": True,
        "fresh_wallet_pct": 55.0,
    }
    bullets = _evidence_bullets(data)
    assert any("Honeypot detected" in b for b in bullets)
    assert any("Bundled launch detected" in b for b in bullets)
    assert any("Sell tax 30%" in b for b in bullets)
    assert any("LP not locked" in b for b in bullets)
    assert any("mintable" in b for b in bullets)
    assert any("55% of supply in fresh wallets" in b for b in bullets)


def test_evidence_bullets_empty_on_no_finding():
    """No red-line finding present → no bullets (never fabricate)."""
    from rugmunchbot.channels.shill_watch import _evidence_bullets

    assert _evidence_bullets({"risk_level": "LOW", "safety_score": 82, "evidence": {}}) == []


@pytest.mark.asyncio
async def test_build_scanner_evidence_off_for_ticker(monkeypatch):
    """Ticker-only shills never resolve a scanner run ({})."""
    from rugmunchbot.channels import shill_watch

    monkeypatch.setattr(shill_watch, "SHILL_SCANNER_EVIDENCE_ENABLED", True)
    assert await shill_watch.build_scanner_evidence("$MEME", "ticker") == {}


@pytest.mark.asyncio
async def test_build_scanner_evidence_run_and_attach(monkeypatch):
    """An address-token campaign gets a scanner evidence dict attached."""
    import rugmunchbot.scanner
    from rugmunchbot.channels import shill_watch

    async def _fake_scan_v2(address, chain):
        return {
            "risk_level": "CRITICAL",
            "safety_score": 12.0,
            "flags": ["Bundled launch"],
            "evidence": {"deployer": {"risk_label": "PROLIFIC", "total_deployments": 20}},
        }

    async def _fake_scan_legacy(address, chain):
        return {}

    monkeypatch.setattr(shill_watch, "SHILL_SCANNER_EVIDENCE_ENABLED", True)
    # build_scanner_evidence does a lazy `from rugmunchbot.scanner import ...`
    # at call time — patch the underlying module, not the shill_watch namespace.
    monkeypatch.setattr(rugmunchbot.scanner, "scan_token_v2", _fake_scan_v2)
    monkeypatch.setattr(rugmunchbot.scanner, "scan_token", _fake_scan_legacy)

    ev = await shill_watch.build_scanner_evidence("0x" + "1" * 40, "ethereum")
    assert ev["scanned"] is True
    assert ev["risk_level"] == "CRITICAL"
    assert any("CRITICAL risk" in b for b in ev["bullets"])
    assert "Deployer history: PROLIFIC" in ev["note"]


def test_format_alert_renders_scanner_evidence():
    """The alert card surfaces on-chain evidence bullet(s) when present."""
    from rugmunchbot.channels.shill_watch import ShillCampaign, format_shill_alert

    campaign = ShillCampaign(
        token="0x" + "9" * 40,
        chain="ethereum",
        accounts=["a_call", "b_alpha"],
        mentions=[{"channel": "a_call", "title": "MOON 100x", "url": "https://x.com/a/status/123"}],
        scanner_evidence={
            "scanned": True,
            "risk_level": "HIGH",
            "bullets": ["Scanner verdict: HIGH risk", "Sell tax 30% — exit is taxed"],
            "note": "Scanner verdict: HIGH risk. Sell tax 30% — exit is taxed.",
        },
    )
    alert = format_shill_alert(campaign)
    assert "On-chain evidence (scanned):" in alert
    assert "Scanner verdict: HIGH risk" in alert
    assert "Sell tax 30%" in alert


# ══════════════════════════════════════════════════════════════════════════
# 2026-09-01 — Telegram KOL post capture (same pipeline, source='telegram')
# ══════════════════════════════════════════════════════════════════════════


def test_tg_post_url_parses():
    """t.me post links normalize to the public t.me/s/ web-preview URL."""
    from rugmunchbot.channels.shill_watch import _tg_post_url

    assert _tg_post_url("https://t.me/CallAnalyser/123") == "https://t.me/s/CallAnalyser/123"
    assert _tg_post_url("https://t.me/CryptoCapoTG/456?single") == "https://t.me/s/CryptoCapoTG/456"
    assert _tg_post_url("https://t.me/CryptoCapoTG") == ""  # no message id
    assert _tg_post_url("https://x.com/foo/status/1") == ""  # not Telegram
    assert _tg_post_url("") == ""


@pytest.mark.asyncio
async def test_capture_telegram_kol_posts_persists_with_source(monkeypatch):
    """A swept KOL t.me post is captured + persisted with source='telegram'."""
    from rugmunchbot.channels import shill_watch

    captured_urls: list[str] = []
    persisted: list[tuple[dict[str, str], dict[str, str]]] = []

    async def fake_capture_and_ocr(url, *, out_path=""):
        captured_urls.append(url)
        return {"url": url, "image_path": out_path, "text": "PUMP $MOON now", "captured": "true"}

    async def fake_persist(campaign, shot):
        persisted.append({"token": campaign.token, "chain": campaign.chain, **shot})
        return True

    monkeypatch.setattr("rugmunchbot.tools.shill_shot.capture_and_ocr", fake_capture_and_ocr)
    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._evidence._persist_shill_evidence", fake_persist
    )
    monkeypatch.setattr("rugmunchbot.channels.shill_watch._evidence.TG_CAPTURE_ENABLED", True)

    article = NewsArticle(
        title="PUMP $MOON now",
        body="PUMP $MOON now",
        source_channel="CallAnalyser",
        source_url="https://t.me/CallAnalyser/123",
        category="alpha",
        entities=["$MOON"],
    )
    n = await shill_watch.capture_telegram_kol_posts([article], budget=2)
    assert n == 1
    assert captured_urls == ["https://t.me/s/CallAnalyser/123"]
    assert len(persisted) == 1
    assert persisted[0]["source"] == "telegram"
    assert persisted[0]["account"] == "CallAnalyser"
    assert persisted[0]["token"] == "$MOON"


@pytest.mark.asyncio
async def test_capture_telegram_kol_posts_disabled_and_budget_zero(monkeypatch):
    """Disabled flag or zero budget → no captures, no pipeline calls."""
    from rugmunchbot.channels import shill_watch

    called = {"n": 0}

    async def fail_capture(url, *, out_path=""):
        called["n"] += 1
        return {"url": url, "image_path": "", "text": "", "captured": "false"}

    monkeypatch.setattr("rugmunchbot.tools.shill_shot.capture_and_ocr", fail_capture)
    monkeypatch.setattr("rugmunchbot.channels.shill_watch._evidence.TG_CAPTURE_ENABLED", False)
    assert await shill_watch.capture_telegram_kol_posts([_article("ch", "hi", ["$MOON"])]) == 0
    monkeypatch.setattr("rugmunchbot.channels.shill_watch._evidence.TG_CAPTURE_ENABLED", True)
    assert await shill_watch.capture_telegram_kol_posts([_article("ch", "hi", [])], budget=0) == 0
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_campaign_capture_uses_tg_preview_for_telegram_mentions(monkeypatch):
    """A campaign mention with only a t.me URL captures the t.me/s/ preview."""
    from rugmunchbot.channels import shill_watch

    captured_urls: list[str] = []
    sources: list[str] = []

    async def fake_capture_and_ocr(url, *, out_path=""):
        captured_urls.append(url)
        return {"url": url, "image_path": out_path, "text": "MOON call", "captured": "true"}

    async def fake_persist(campaign, shot):
        sources.append(shot.get("source", ""))
        return True

    monkeypatch.setattr("rugmunchbot.tools.shill_shot.capture_and_ocr", fake_capture_and_ocr)
    monkeypatch.setattr(
        "rugmunchbot.channels.shill_watch._evidence._persist_shill_evidence", fake_persist
    )
    monkeypatch.setattr("rugmunchbot.channels.shill_watch._evidence.SHILL_SCREENSHOT_ENABLED", True)

    campaign = ShillCampaign(
        token="$MOON",
        chain="ticker",
        accounts=["CallAnalyser"],
        mentions=[
            {"channel": "CallAnalyser", "title": "MOON", "url": "https://t.me/CallAnalyser/7"}
        ],
    )
    n = await shill_watch._capture_campaign_shills(campaign, budget=1)
    assert n == 1
    assert captured_urls == ["https://t.me/s/CallAnalyser/7"]
    assert sources == ["telegram"]
