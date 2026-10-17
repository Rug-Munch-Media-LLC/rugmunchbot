"""Test wave 17b — alerts channel formatters (pure rendering).

Contracts read from source (2026-08-22). Pure functions only: no network,
no DB, no Telegram.
"""

from __future__ import annotations

from rugmunchbot.channels.formatters._alerts import (
    _explorer_url,
    _item_links,
    _scan_reason_bits,
    _scanner_url,
    alert_verdict,
    format_alerts_post,
    format_main_escalation,
)
from rugmunchbot.formatters import gmgn_url

_A = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"

# ── deep-link builders ───────────────────────────────────────────────────────


def test_scanner_url_builder() -> None:
    assert _scanner_url("", "eth") == ""
    assert _scanner_url(_A, "ethereum") == f"https://rugmunch.io/scanner?token={_A}&chain=ethereum"
    assert _scanner_url(_A, "") == f"https://rugmunch.io/scanner?token={_A}&chain="


def test_explorer_url_solana_only() -> None:
    assert _explorer_url("", "solana") == ""
    assert _explorer_url("WalletAddr", "SOLANA") == "https://solscan.io/account/WalletAddr"
    assert _explorer_url(_A, "ethereum") == ""  # wallets only render for solana


def test_item_links_prefers_payload_and_falls_back() -> None:
    # No address at all -> no links.
    assert _item_links({}) == ""
    # Address present -> built scan + GMGN chart links.
    links = _item_links({"address": _A, "chain": "ethereum"})
    assert links == (
        f'<a href="https://rugmunch.io/scanner?token={_A}&amp;chain=ethereum">🔍 Scan</a>'
        f' · <a href="{gmgn_url(_A, "ethereum")}">📊 Chart</a>'
    )
    # Payload-supplied URLs win and explorer renders only when carried.
    custom = _item_links(
        {
            "scanner_url": "https://custom.example/s",
            "chart_url": "https://custom.example/c",
            "explorer_url": "https://solscan.io/account/w",
        }
    )
    assert custom == (
        '<a href="https://custom.example/s">🔍 Scan</a>'
        ' · <a href="https://custom.example/c">📊 Chart</a>'
        ' · <a href="https://solscan.io/account/w">🧭 Explorer</a>'
    )


# ── verdict derivation ───────────────────────────────────────────────────────


def test_alert_verdict_known_flag_wins() -> None:
    assert (
        alert_verdict({"risk_flags": ["HONEYPOT"]})
        == "honeypot — sells can be blocked after buying"
    )
    assert (
        alert_verdict({"risk_flags": ["lp_removed_event"]})
        == "LP removed — liquidity can be yanked at any time"
    )


def test_alert_verdict_risk_level_and_fallback() -> None:
    assert (
        alert_verdict({"risk_flags": ["totally_unknown"], "risk_level": "CRITICAL"})
        == "critical-risk verdict from the scanner — check before buying"
    )
    assert alert_verdict({}) == "flagged by the RugMunch scanner — verify before touching it"


def test_scan_reason_bits_maps_dedupes_and_limits() -> None:
    bits = _scan_reason_bits({"risk_flags": ["HONEYPOT", "mintable", "weird_flag"]})
    assert bits == [
        "honeypot: sells can be blocked",
        "mintable: supply can be inflated",
        "weird_flag",
    ]
    one = _scan_reason_bits({"risk_flags": ["HONEYPOT", "mintable"]}, limit=1)
    assert one == ["honeypot: sells can be blocked"]


def test_scan_reason_bits_security_lp_unlocked_and_empty_fallback() -> None:
    assert _scan_reason_bits({"security": {"lp_locked": False}}) == [
        "LP unlocked: can be yanked anytime"
    ]
    assert _scan_reason_bits({}) == ["flagged by the RugMunch scanner — verify before touching it"]


# ── post assembly ────────────────────────────────────────────────────────────


def test_format_alerts_post_all_clear() -> None:
    post = format_alerts_post([])
    assert post.startswith("\U0001f6a8 <b>Risk Alerts</b>")
    assert "All clear" in post
    assert post.image_url == ""


def test_format_alerts_post_renders_item_blocks() -> None:
    item = {
        "symbol": "PEPE",
        "name": "Pepe",
        "address": _A,
        "chain": "ethereum",
        "risk_level": "high",
        "alert_type": "scanner",
        "risk_flags": ["honeypot"],
        "image_url": "https://img.example/x.png",
    }
    post = format_alerts_post([item])
    assert "🚨 <b>Alert — Scanner</b>" in post
    assert "<b>Pepe</b> (PEPE)" in post
    assert f"<code>{_A}</code>" in post
    assert "⚡ Condition: flagged high" in post  # score-less fallback wording
    assert "💡 honeypot — sells can be blocked after buying" in post
    assert "⚠️ honeypot" in post
    assert "⛓️ ethereum" in post
    assert f"/scan <code>{_A}</code>" in post
    assert "x.png" in post.image_url
    assert "/api/v1/media/thumb?url=" in post.image_url


def test_format_alerts_post_condition_from_risk_score() -> None:
    post = format_alerts_post([{"symbol": "X", "risk_score": 88, "risk_level": "high"}])
    assert "⚡ Condition: risk 88/100 (flagged high)" in post


def test_format_main_escalation_card() -> None:
    item = {
        "name": "Pepe",
        "symbol": "PEPE",
        "address": _A,
        "chain": "solana",
        "condition": "lp drained",
        "risk_flags": ["honeypot"],
        "image_url": "https://img.example/y.png",
    }
    post = format_main_escalation(item)
    assert post.startswith("🚨 <b>HIGH SIGNAL</b>")
    # Headline is wrapped in the built scanner deep link.
    assert (
        f'<a href="https://rugmunch.io/scanner?token={_A}&amp;chain=solana">Pepe (PEPE)</a>' in post
    )
    assert f"<code>{_A}</code>" in post
    assert "⚡ <b>Condition met:</b> lp drained" in post
    assert "💡 <b>Why it matters:</b> honeypot — sells can be blocked after buying" in post
    assert "⛓️ solana" in post
    assert "y.png" in post.image_url
    assert "/api/v1/media/thumb?url=" in post.image_url


def test_format_main_escalation_minimal_item() -> None:
    post = format_main_escalation({"title": "Weird Signal"})
    assert "Weird Signal" in post
    # A minimal item with no story image still carries the alert brand
    # graphic (alert → current_cryptorugmuncher.png) — never "".
    assert post.image_url == "https://rugmunch.io/brand/current_cryptorugmuncher.png"
