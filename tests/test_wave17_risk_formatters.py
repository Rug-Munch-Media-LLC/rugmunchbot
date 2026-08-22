"""Test wave 17 — risk visualization formatters (pure rendering).

Contracts read from source (2026-08-22). Pure functions only: no network,
no DB, no Telegram.
"""

from __future__ import annotations

from rugmunchbot.formatters._base._primitives import thin_sep
from rugmunchbot.formatters._base._risk import (
    RISK_BAR_EMPTY,
    RISK_BAR_FILLED,
    RISK_EMOJI,
    RISK_UNKNOWN_EMOJI,
    _pct_emoji,
    _price_diff_pct,
    _risk_indicator,
    _risk_level,
    _verdict_sentence,
    format_funding_analysis,
    format_scoring_breakdown,
    format_verdict_header,
)

_SEP = thin_sep()

# ── level mapping ────────────────────────────────────────────────────────────


def test_risk_level_boundaries() -> None:
    assert _risk_level(70.0) == "high"
    assert _risk_level(69.9) == "medium"
    assert _risk_level(40.0) == "medium"
    assert _risk_level(39.9) == "low"


def test_risk_indicator_colors() -> None:
    assert _risk_indicator(90) == RISK_EMOJI["high"]
    assert _risk_indicator(50) == RISK_EMOJI["medium"]
    assert _risk_indicator(10) == RISK_EMOJI["low"]


def test_pct_emoji_thresholds_are_strict() -> None:
    # Strictly greater-than: a value exactly at the threshold is NOT red.
    assert _pct_emoji(50.01, high=50, mid=30) == RISK_EMOJI["high"]
    assert _pct_emoji(50.0, high=50, mid=30) == RISK_EMOJI["medium"]
    assert _pct_emoji(30.0, high=50, mid=30) == RISK_EMOJI["low"]


# ── verdict sentence priority chain ──────────────────────────────────────────


def test_verdict_sentence_priority_order() -> None:
    assert "Honeypot" in _verdict_sentence({"honeypot": True, "mintable": True})
    assert "mintable" in _verdict_sentence({"mintable": True, "owner_renounced": False})
    assert "Ownership is not renounced" in _verdict_sentence({"owner_renounced": False})
    assert "blacklist" in _verdict_sentence({"can_blacklist": True})
    assert "not verified" in _verdict_sentence({"contract_verified": False})


def test_verdict_sentence_thin_liquidity_and_clean_bill() -> None:
    assert "Liquidity is thin" in _verdict_sentence({"liquidity_usd": 5000})
    assert "No critical flags" in _verdict_sentence({})
    # Absent keys are never treated as False — no false claims.
    assert "No critical flags" in _verdict_sentence({"honeypot": False, "mintable": None})
    # Junk liquidity values degrade to the clean bill, not an exception.
    assert "No critical flags" in _verdict_sentence({"liquidity_usd": "junk"})


# ── verdict header ───────────────────────────────────────────────────────────


def test_verdict_header_scored_and_unknown() -> None:
    out = format_verdict_header({"risk_score": 82, "honeypot": True})
    assert out.startswith(f"{RISK_EMOJI['high']} <b>HIGH RISK</b> \u2014 Score 82/100\n")
    assert "Honeypot detected" in out
    unknown = format_verdict_header({})
    assert unknown.startswith(f"{RISK_UNKNOWN_EMOJI} <b>UNKNOWN</b>")
    junk = format_verdict_header({"risk_score": "abc"})
    assert "<b>UNKNOWN</b>" in junk


# ── price diff helper ────────────────────────────────────────────────────────


def test_price_diff_pct_cases() -> None:
    assert _price_diff_pct(0, 100) == "N/A"
    assert _price_diff_pct(100, 100) == "\u2248 same"
    assert _price_diff_pct(90, 100) == "T1 10.00% cheaper"
    assert _price_diff_pct(120, 100) == "T2 16.67% cheaper"


# ── scoring breakdown ────────────────────────────────────────────────────────


def test_scoring_breakdown_empty_is_blank() -> None:
    assert format_scoring_breakdown({}) == ""
    # Non-dict dimension entries are skipped but the header block still renders.
    out = format_scoring_breakdown({"unknown_dim": "junk"})
    assert "Scoring Breakdown" in out
    assert "junk" not in out


def test_scoring_breakdown_renders_bars_flags_and_deltas() -> None:
    breakdown = {
        "security": {
            "score": 80,
            "max": 100,
            "delta": 5,
            "flags": ["honeypot-ish", "tax drift", "extra"],
        },
        "liquidity": {"score": 20, "max": 100, "delta": -3},
        "holders": "not-a-dict",
    }
    out = format_scoring_breakdown(breakdown)
    lines = out.split("\n")
    assert lines[0] == ""
    assert "Scoring Breakdown" in lines[1]
    assert lines[2] == _SEP
    sec = lines[3]
    assert RISK_EMOJI["high"] in sec  # ratio 80% -> red
    assert f"<code>{RISK_BAR_FILLED * 8}{RISK_BAR_EMPTY * 2}</code>" in sec
    assert "<b>80/100</b>" in sec
    assert sec.endswith(" \u25b2")  # positive delta arrow
    # Flag lines render directly beneath their dimension row (lines 4-5),
    # so the liquidity row lands at index 6.
    liq = lines[6]
    assert RISK_EMOJI["low"] in liq  # ratio 20% -> green
    assert liq.endswith(" \u25bc")
    # Only the first two flags render, each on its own warning line.
    assert "\u26a0\ufe0f honeypot-ish" in out
    assert "\u26a0\ufe0f tax drift" in out
    assert "extra" not in out
    # Non-dict dimension entries are skipped entirely.
    assert "Holders" not in out


def test_scoring_breakdown_zero_max_never_divides() -> None:
    out = format_scoring_breakdown({"market": {"score": 0, "max": 0}})
    assert "<b>0/0</b>" in out
    assert RISK_BAR_EMPTY * 10 in out


# ── funding analysis ─────────────────────────────────────────────────────────


def test_funding_analysis_empty_when_no_data() -> None:
    assert format_funding_analysis({}) == ""
    assert format_funding_analysis({"exchange_funded_pct": None}) == ""


def test_funding_analysis_renders_present_and_missing_metrics() -> None:
    out = format_funding_analysis(
        {"exchange_funded_pct": 60, "fresh_wallet_pct": 25, "holder_overlap_pct": None}
    )
    lines = out.split("\n")
    assert lines[0] == ""
    assert "Funding Analysis" in lines[1]
    assert lines[2] == _SEP
    assert f"  {RISK_EMOJI['high']} Exchange-Funded:        <b>60%</b>" in out
    assert f"  {RISK_EMOJI['medium']} Fresh Wallets:           <b>25%</b>" in out
    assert f"  {RISK_UNKNOWN_EMOJI} Holder Overlap w/ Scams: <b>N/A</b>" in out
