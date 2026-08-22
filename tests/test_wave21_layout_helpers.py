"""Test wave 21 — scan-report layout helpers + honest verdict rows.

Contracts read from source (2026-08-22). Pure functions only.
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_common._layout import (
    _bundle_verdict_row,
    _distribution_verdict,
    _fake_volume_verdict_text,
    _fmt_bool_emoji,
    _fmt_market_value,
    _fmt_num,
    _holder_distribution_rows,
    _risk_emoji,
    _risk_level_word,
)
from rugmunchbot.formatters._sentinel_helpers import _fmt_usd_amount

# ── primitives ───────────────────────────────────────────────────────────────


def test_risk_level_word_and_emoji_bands() -> None:
    assert _risk_level_word(None) == "UNKNOWN"
    assert _risk_level_word(70) == "HIGH"
    assert _risk_level_word(69.9) == "MEDIUM"
    assert _risk_level_word(40) == "MEDIUM"
    assert _risk_level_word(39.9) == "LOW"
    assert _risk_emoji(None) == "\u26aa"
    assert _risk_emoji(80) == "\U0001f534"
    assert _risk_emoji(50) == "\U0001f7e1"
    assert _risk_emoji(10) == "\U0001f7e2"


def test_fmt_bool_emoji_danger_orientation() -> None:
    assert _fmt_bool_emoji(None) == "\u26aa"
    assert _fmt_bool_emoji(True) == "\U0001f534"  # danger on
    assert _fmt_bool_emoji(False) == "\U0001f7e2"
    # Inverted semantics: True is SAFE when danger_on=False.
    assert _fmt_bool_emoji(True, danger_on=False) == "\U0001f7e2"
    assert _fmt_bool_emoji(False, danger_on=False) == "\U0001f534"


def test_fmt_num_and_market_value() -> None:
    assert _fmt_num(1234567) == "1,234,567"
    assert _fmt_num(5, suffix="x") == "5x"
    assert _fmt_num("junk") == "N/A"
    # Absent/junk render as an em-dash; a REAL zero renders as $0.
    assert _fmt_market_value(None) == "\u2014"
    assert _fmt_market_value("junk") == "\u2014"
    assert _fmt_market_value(0) == "$0"
    assert _fmt_market_value(1234.56) == _fmt_usd_amount(1234.56)


# ── bundle verdict ───────────────────────────────────────────────────────────


def test_bundle_verdict_row_states() -> None:
    assert _bundle_verdict_row({}) is None  # no payload at all
    bundled = _bundle_verdict_row(
        {"bundle_analysis": {"is_bundled": True, "confidence": 88, "risk_label": "severe"}}
    )
    assert bundled == "🔴 Bundle detected: · 88% conf · SEVERE"
    with_count = _bundle_verdict_row(
        {
            "bundle_analysis": {
                "is_bundled": True,
                "confidence": 50,
                "launch_buyers": 7,
                "risk_label": "<high>",
            }
        }
    )
    # Quirk: risk_label is html.escape()d THEN uppercased, so entity
    # letters come out uppercased (&LT;…&GT; — still valid entity casing).
    assert with_count == "🔴 Bundle detected: <b>7</b> wallets · 50% conf · &LT;HIGH&GT;"
    clean = _bundle_verdict_row({"bundle_analysis": {"result": "Scanned"}})
    assert clean == "🟢 No bundle detected"
    nodata = _bundle_verdict_row({"bundle_analysis": {"result": "unavailable"}})
    assert nodata == "\u2014"  # never claims "no bundle" without a scan


# ── distribution verdict ─────────────────────────────────────────────────────


def test_distribution_verdict_top10_bands() -> None:
    ha = lambda pct: {"holder_analysis": {"top_10_pct": pct}}  # noqa: E731
    assert _distribution_verdict(ha(60)) == ("\U0001f534", "CONCENTRATED")
    assert _distribution_verdict(ha(35)) == ("\U0001f7e1", "MODERATE")
    assert _distribution_verdict(ha(20)) == ("\U0001f7e2", "HEALTHY")


def test_distribution_verdict_hhi_fallback_and_none() -> None:
    assert _distribution_verdict({"holder_analysis": {"hhi": 3500}}) == (
        "\U0001f534",
        "CONCENTRATED",
    )
    assert _distribution_verdict({"holder_analysis": {"hhi": 2000}}) == (
        "\U0001f7e1",
        "MODERATE",
    )
    assert _distribution_verdict({"holder_analysis": {"hhi": 500}}) == (
        "\U0001f7e2",
        "HEALTHY",
    )
    # bundle_analysis carries a legacy top10 field when holder_analysis is mute.
    assert _distribution_verdict({"bundle_analysis": {"top10_holder_pct": 55}}) == (
        "\U0001f534",
        "CONCENTRATED",
    )
    assert _distribution_verdict({}) is None


def test_holder_distribution_rows_assembly() -> None:
    r = {
        "holder_analysis": {"top_10_pct": 55.0, "top_20_pct": 70.0},
        "wallet_metrics": {"result": "ok", "cex_funded_pct": 12.5},
        "gmgn": {"holders": {"sniper_count": 3, "insider_count": 0}},
    }
    rows = _holder_distribution_rows(r)
    assert rows[0] == "  🐋 Top-10 hold <b>55.0%</b> · Top-20 <b>70.0%</b>"
    assert rows[1] == "  🏦 Exchange-funded <b>12.5%</b> of analyzed holders"
    assert rows[2] == "  🎯 <b>3</b> snipers"  # zero insider count omitted
    assert rows[3] == "  🔴 Distribution verdict: <b>CONCENTRATED</b>"
    assert _holder_distribution_rows({}) == []


# ── fake volume verdict ──────────────────────────────────────────────────────


def test_fake_volume_verdict_no_data_is_dash() -> None:
    assert _fake_volume_verdict_text({}) == "\u2014"
    assert _fake_volume_verdict_text({"wash_trading": {}}) == "\u2014"


def test_fake_volume_verdict_likely_with_detail_and_churn() -> None:
    r = {
        "wash_trading": {
            "fake_volume_verdict": "likely",
            "fake_volume_detail": "self-deals: 14 · volume 12x holders",
        },
        "volume_24h": 250_000,
        "price_change_24h": 1.0,
    }
    out = _fake_volume_verdict_text(r)
    assert out == (
        "🔴 <b>Likely</b> (wash-trading detected: "
        "self-deals: 14 · volume 12x holders · vol up, price flat)"
    )


def test_fake_volume_verdict_legacy_pct_and_organic() -> None:
    # Legacy payload: percentage only -> verdict derived from it.
    high = _fake_volume_verdict_text({"wash_trading": {"fake_volume_pct": 30}})
    assert high == "🔴 <b>Likely</b> (wash-trading detected: 30% fabricated)"
    low = _fake_volume_verdict_text({"wash_trading": {"fake_volume_pct": 5}})
    assert low == "🟢 Organic volume patterns"
    # Explicit organic verdict wins regardless of detail strings.
    organic = _fake_volume_verdict_text(
        {"wash_trading": {"fake_volume_verdict": "organic", "fake_volume_detail": "x"}}
    )
    assert organic == "🟢 Organic volume patterns"
