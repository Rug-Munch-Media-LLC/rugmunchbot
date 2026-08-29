"""Test wave 18 — alpha channel formatters (pure rendering).

Contracts read from source (2026-08-22). Pure functions only: no network,
no DB, no Telegram.
"""

from __future__ import annotations

from rugmunchbot.channels.formatters._alpha import (
    _fmt_age_hours,
    _fmt_usd,
    _fmt_wallet_short,
    _launch_verdict,
    format_alpha_post,
    format_scan_card,
)
from rugmunchbot.channels.formatters._primitives import _risk_emoji
from rugmunchbot.formatters import short_addr

_A = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"

# ── small formatters ─────────────────────────────────────────────────────────


def test_fmt_usd_and_age() -> None:
    assert _fmt_usd(1234567) == "$1,234,567"
    assert _fmt_usd(None) == ""
    assert _fmt_usd("junk") == ""
    assert _fmt_age_hours(0.5) == "⏱ 30m old"
    assert _fmt_age_hours(36) == "⏱ 36h old"
    assert _fmt_age_hours("junk") == ""


def test_fmt_wallet_short_placeholder() -> None:
    assert _fmt_wallet_short(_A) == short_addr(_A)
    assert _fmt_wallet_short("") == "?"


def test_launch_verdict_levels() -> None:
    assert "critical" in _launch_verdict({"risk_level": "CRITICAL"})
    assert "high" in _launch_verdict({"risk_level": "high"})
    assert "medium" in _launch_verdict({"risk_level": "medium"})
    assert "low" in _launch_verdict({"risk_level": "low"})
    assert _launch_verdict({}) == "new launch — verify before buying"


# ── scan card ────────────────────────────────────────────────────────────────


def test_scan_card_label_honesty_rules() -> None:
    # Both known and distinct -> "Name (SYM)".
    both = format_scan_card({"name": "Pepe Coin", "symbol": "PEPE", "risk_level": "high"})
    assert "<b>Pepe Coin (PEPE)</b>" in both
    # Symbol identical to name (case-insensitive) -> symbol only, no echo.
    # The card renders the symbol VERBATIM (no case normalization).
    same = format_scan_card({"name": "PEPE", "symbol": "pepe", "risk_level": "high"})
    assert "<b>pepe</b>" in same
    assert "(pepe)" not in same
    # Placeholder fields render as Unnamed token, never "Unknown (???)".
    unnamed = format_scan_card({"name": "???", "symbol": "unknown", "risk_level": "low"})
    assert "<b>Unnamed token</b>" in unnamed


def test_scan_card_score_line_and_fallback_verdict() -> None:
    scored = format_scan_card({"symbol": "PEPE", "risk_level": "high", "risk_score": 82})
    assert f"{_risk_emoji('high')} <b>Risk Score: 82/100</b> \u2014 HIGH" in scored
    unscored = format_scan_card({"symbol": "PEPE", "risk_level": "high"})
    assert f"{_risk_emoji('high')} <b>Verdict: HIGH</b>" in unscored


def test_scan_card_full_address_reasons_commentary_and_image() -> None:
    item = {
        "name": "Pepe",
        "symbol": "PEPE",
        "address": _A,
        "chain": "ethereum",
        "risk_level": "critical",
        "risk_flags": ["HONEYPOT"],
        "commentary": "exit liquidity looks fictional",
        "image_url": "https://img.example/z.png",
    }
    post = format_scan_card(item)
    assert f"<code>{_A}</code>" in post  # FULL address, never truncated
    assert "⚠️ honeypot: sells can be blocked" in post
    assert "\U0001f4ac \u201cexit liquidity looks fictional\u201d" in post
    assert f"\U0001f916 <b>Scan with @RugMunchBot:</b> /scan <code>{_A}</code>" in post
    assert post.startswith("\U0001f52c <b>Suspicious Tokens &amp; Scans of Interest</b>")
    assert post.image_url == "https://img.example/z.png"


def test_scan_card_pending_teaser_says_scanning_never_unavailable() -> None:
    """A deep-scan-still-running card reads "Scanning…" and carries NO
    RiskReport block — never "Deep analysis unavailable" (scans-of-interest
    rule: post "scanning…" and re-post later, not "unavailable")."""
    item = {
        "symbol": "PEPE",
        "address": _A,
        "chain": "ethereum",
        "risk_level": "high",
        "risk_score": 59,
        "risk_flags": ["thin_liquidity", "violent_moves", "unscanned"],
        "liquidity_usd": 5000.0,
        "scan_pending": True,
    }
    post = format_scan_card(item)
    assert "\u23f3 <i>Scanning\u2026" in post
    assert "Deep analysis unavailable" not in post
    assert "unavailable" not in post
    assert "<b>Risk Report</b>" not in post  # no contradictory 1/30-factor composite
    # Whatever signal the fast path carried is still shown.
    assert "<b>$5,000</b> liq" in post


def test_scan_card_shows_the_actual_signal_fields() -> None:
    """Every posted token must show the actual signal: holders count,
    liquidity amount, volume, first-25-buyers, bundle/deployer flags."""
    item = {
        "name": "Signal",
        "symbol": "SIG",
        "address": _A,
        "chain": "ethereum",
        "risk_level": "medium",
        "risk_score": 46,
        "risk_flags": ["thin_liquidity"],
        "holders": 412,
        "liquidity_usd": 18000.0,
        "volume_24h": 95000.0,
        "first_buyers": [{"address": "0xholder2"}],
        "bundle_analysis": {"is_bundled": True, "launch_cluster_score": 74.0},
        "deployer_address": "0xDeployer00000000000000000000000000000001",
        "churned": True,
    }
    post = format_scan_card(item)
    assert "<b>412</b> holders" in post
    assert "<b>$18,000</b> liq" in post
    assert "<b>$95,000</b> vol 24h" in post
    assert "first-<b>1</b> buyers" in post
    assert "bundle <b>DETECTED</b>" in post
    assert "deployer <code>" in post


def test_scan_card_header_reconciles_with_body_composite() -> None:
    """The card header risk score and the body RiskReport composite MUST
    agree — the scans-of-interest rule (never 59/100 HIGH + 0/100 OK). The
    churn rail sets ``risk_score`` to the composite the builder computes for
    the SAME payload, so the header and the body block render one number."""
    from rugmunchbot.formatters._scan_report_riskreport import _build_risk_report_from_scan

    item = {
        "name": "Fast",
        "symbol": "FST",
        "address": _A,
        "chain": "ethereum",
        "risk_level": "medium",
        "risk_flags": ["thin_liquidity"],
        "holders": 412,
        "liquidity_usd": 18000.0,
        "volume_24h": 95000.0,
        "first_buyers": [{"address": "0xholder2"}],
        "bundle_analysis": {"is_bundled": True, "launch_cluster_score": 74.0},
        "deployer_address": "0xDeployer00000000000000000000000000000001",
        "deployer_profile": {"total_deployments": 6, "rug_rate": 0.7},
        "wallet_metrics": {"fresh_wallet_pct": 55.0, "cex_funded_pct": 8.0},
        "honeypot": False,
        "mintable": False,
        "owner_renounced": True,
        "buy_tax": 0.12,
        "sell_tax": 0.18,
        "lp_locked": 0.12,
        "liquidity_verification": {"total_locked_percentage": 12.0},
        "holder_overlap": {"max_overlap_pct": 41.0, "worst_token_is_scam": True},
        "evidence": {"security": {"is_honeypot": False}},
        "churned": True,
    }
    rep = _build_risk_report_from_scan(item)
    assert rep is not None
    assert rep["composite_score"] is not None
    item["risk_score"] = rep["composite_score"]
    post = format_scan_card(item)
    import re

    header = re.search(r"Risk Score: (\d+)/100", post)
    body = re.search(r"Composite Risk:</b> <b>(\d+)/100", post)
    assert header is not None
    assert body is not None
    assert header.group(1) == body.group(1), "header score must equal body composite"


# ── alpha digest assembly ────────────────────────────────────────────────────


def test_alpha_post_empty_window_has_no_invented_sections() -> None:
    post = format_alpha_post([], [], [])
    assert post.startswith("\U0001f40b <b>Alpha Feed</b>")
    assert "No fresh alpha signals" in post
    assert "Whale Moves" not in post
    assert post.image_url == ""


def test_alpha_post_whale_section_renders_signal_first() -> None:
    move = {
        "title": "Whale bought PEPE",
        "chain": "ethereum",
        "token_address": _A,
        "body": "Accumulated **4.2T** tokens in one sweep.",
    }
    post = format_alpha_post([move], [], [])
    assert "⚡ <b>Whale Moves</b>" in post
    assert "• Whale bought PEPE (ethereum)" in post
    assert "💬 Accumulated 4.2T tokens in one sweep." in post  # markup stripped
    assert f"🤖 /scan <code>{_A}</code>" in post


def test_alpha_post_smart_money_tags_pnl_explorer() -> None:
    wallet = {
        "label": "Insider Cluster",
        "address": "WalletAddrSol",
        "chain": "solana",
        "pnl_usd": 250000,
        "tags": ["sniper", "bundled", "kol", "mev", "extra-tag"],
    }
    post = format_alpha_post([], [wallet], [])
    assert "🧠 <b>Smart Money Wallets</b>" in post
    assert "• <b>Insider Cluster</b> — <code>WalletAddrSol</code> · P&L $250,000" in post
    assert "🏷 sniper · bundled · kol · mev" in post  # first four tags only
    assert '🧭 <a href="https://solscan.io/account/WalletAddrSol">Explorer</a>' in post


def test_alpha_post_launch_meta_and_image_pick() -> None:
    launch = {
        "symbol": "NEW",
        "name": "New Token",
        "chain": "solana",
        "risk_level": "medium",
        "address": _A,
        "age_hours": 2,
        "liquidity_usd": 30000,
    }
    with_image = dict(launch, image_url="https://img.example/w.png")
    post = format_alpha_post([], [], [with_image])
    assert "🆕 <b>New Launches</b>" in post
    assert f"{_risk_emoji('medium')} <b>New Token</b> (NEW) — solana" in post
    assert "💡 medium scan verdict — check liquidity and holder concentration" in post
    assert "⏱ 2h old · 💧 $30,000 liq" in post
    assert post.image_url == "https://img.example/w.png"


def test_alpha_post_image_skips_unusable_urls() -> None:
    post = format_alpha_post([{"title": "x", "image_url": "ftp://nope"}], [], [])
    assert post.image_url == ""
