"""Test wave 12 — market_condition verdict engine, alerts monitor deltas,
daily_context formatters.

Contracts read from source (2026-08-22). Pure logic only: no HTTP, no
backend, no Telegram.
"""

from __future__ import annotations

import time

from rugmunchbot.daily_context import (
    _fmt_mcap,
    _fmt_price,
    _has_content,
    _launch_label,
    _trending_label,
)
from rugmunchbot.handlers.alerts import (
    _check_alert_condition,
    _monitor_baseline,
    _monitor_deltas,
    _pct,
)
from rugmunchbot.tools.market_condition import (
    _as_float,
    _chain_slug,
    _fmt_pct,
    _lean,
    _tvl_change_7d,
    _vote,
    assess_market_condition,
)

# ── market_condition: small helpers ──────────────────────────────────────────


def test_chain_slug_normalizes_and_maps_exceptions() -> None:
    assert _chain_slug("  Solana ") == "solana"
    assert _chain_slug("BNB") == "bsc"
    assert _chain_slug("binance smart chain") == "bsc"
    assert _chain_slug("ethereum") == "ethereum"
    assert _chain_slug("") == ""


def test_vote_risk_on_wins_ties() -> None:
    assert _vote(True, True) == 1
    assert _vote(True, False) == 1
    assert _vote(False, True) == -1
    assert _vote(False, False) == 0


def test_lean_labels() -> None:
    assert _lean(1) == "supporting risk-on"
    assert _lean(-1) == "supporting risk-off"
    assert _lean(0) == "neutral"


def test_fmt_pct_signed_two_decimals() -> None:
    assert _fmt_pct(2.5) == "+2.50%"
    assert _fmt_pct(-3.0) == "-3.00%"
    assert _fmt_pct(0) == "+0.00%"


def test_as_float_coerces_real_values_only() -> None:
    assert _as_float("29") == 29.0
    assert _as_float(" 29 ") == 29.0
    assert _as_float(29) == 29.0
    assert _as_float(True) is None  # bools are not numbers here
    assert _as_float("high") is None
    assert _as_float(None) is None
    assert _as_float([]) is None


def _pt(days_ago: float, tvl: float) -> dict[str, float]:
    return {"date": time.time() - days_ago * 86400, "tvl": tvl}


def test_tvl_change_over_lookback_window() -> None:
    series = [_pt(30, 100.0), _pt(2, 80.0), _pt(0, 120.0)]
    # Reference = first point at/after the 7d cutoff (the 80 point).
    assert _tvl_change_7d(series, 7) == 50.0


def test_tvl_change_falls_back_to_oldest_point_outside_window() -> None:
    series = [_pt(90, 100.0), _pt(89, 200.0)]
    assert _tvl_change_7d(series, 7) == 100.0


def test_tvl_change_never_invents_numbers() -> None:
    assert _tvl_change_7d([_pt(1, 100.0)], 7) is None  # needs two points
    assert _tvl_change_7d([], 7) is None
    assert _tvl_change_7d([_pt(30, 100.0), {"date": 1}], 7) is None  # bad last
    assert _tvl_change_7d([_pt(30, 100.0), _pt(1, 0.0)], 7) is None  # tvl <= 0
    assert (
        _tvl_change_7d([{"date": time.time() - 90 * 86400, "tvl": 0}, _pt(89, 200.0)], 7) is None
    )  # fallback point invalid too


# ── market_condition: verdict engine ─────────────────────────────────────────


def _all_on_signals() -> dict[str, object]:
    return {
        "fear_greed": {"value": 70, "classification": "Greed"},
        "btc": {"change_24h": 5.0},
        "eth": {"change_24h": 4.0},
        "global_market_cap_change_24h": 3.0,
        "breadth": {"pump_ratio": 0.7, "pumping": 10, "dumping": 2},
        "tvl_trend": {"median_change_7d": 8.0, "chains_measured": 8},
    }


def test_assess_all_risk_on_signals() -> None:
    out = assess_market_condition(_all_on_signals())
    assert out["verdict"] == "risk_on"
    assert out["confidence"] == "high"
    assert out["score"] == 1.0
    assert len(out["reasons"]) == 6
    assert "Fear & Greed 70 (Greed)" in out["reasons"][0]


def test_assess_all_risk_off_signals() -> None:
    signals: dict[str, object] = {
        "fear_greed": {"value": 25, "classification": "Extreme Fear"},
        "btc": {"change_24h": -6.0},
        "eth": {"change_24h": -4.0},
        "global_market_cap_change_24h": -3.5,
        "breadth": {"pump_ratio": 0.2, "pumping": 1, "dumping": 9},
        "tvl_trend": {"median_change_7d": -9.0, "chains_measured": 8},
    }
    out = assess_market_condition(signals)
    assert out["verdict"] == "risk_off"
    assert out["score"] == -1.0
    assert out["confidence"] == "high"


def test_assess_empty_signals_is_neutral_low() -> None:
    assert assess_market_condition({}) == {
        "verdict": "neutral",
        "confidence": "low",
        "score": 0.0,
        "reasons": [],
    }


def test_assess_mixed_signals_cancel_to_neutral() -> None:
    out = assess_market_condition(
        {
            "fear_greed": {"value": 50},  # neutral band vote 0
            "btc": {"change_24h": 3.0},  # +1
            "eth": {"change_24h": -3.0},  # -1
        }
    )
    assert out["verdict"] == "neutral"
    assert out["score"] == 0.0
    assert out["confidence"] == "medium"  # 3 signals present
    assert len(out["reasons"]) == 3


def test_assess_threshold_boundaries_are_inclusive() -> None:
    on = assess_market_condition({"fear_greed": {"value": 55}})
    assert on["score"] == 1.0  # >= 55 counts risk-on
    off = assess_market_condition({"fear_greed": {"value": 40}})
    assert off["score"] == -1.0  # <= 40 counts risk-off
    mid = assess_market_condition({"fear_greed": {"value": 45}})
    assert mid["score"] == 0.0
    btc_edge_on = assess_market_condition({"btc": {"change_24h": 2.0}})
    assert btc_edge_on["score"] == 1.0
    btc_edge_off = assess_market_condition({"btc": {"change_24h": -2.0}})
    assert btc_edge_off["score"] == -1.0


def test_assess_ignores_non_numeric_and_malformed_signals() -> None:
    # String F&G value is NOT coerced by the verdict engine — absent signal.
    out = assess_market_condition({"fear_greed": {"value": "70"}})
    assert out == {"verdict": "neutral", "confidence": "low", "score": 0.0, "reasons": []}
    # Non-dict signal entries are ignored the same way.
    out2 = assess_market_condition({"btc": "+5%", "breadth": "hot"})
    assert out2["reasons"] == []


# ── alerts: condition + monitor deltas ───────────────────────────────────────


def test_alert_condition_directions() -> None:
    assert _check_alert_condition(105.0, 100.0, "above") is True
    assert _check_alert_condition(100.0, 100.0, "above") is True  # >= boundary
    assert _check_alert_condition(95.0, 100.0, "above") is False
    assert _check_alert_condition(95.0, 100.0, "below") is True
    assert _check_alert_condition(100.0, 100.0, "below") is True
    assert _check_alert_condition(105.0, 100.0, "below") is False


def test_alert_condition_percent_modes() -> None:
    assert _check_alert_condition(106.0, 100.0, "change_5") is True  # +6%
    assert _check_alert_condition(104.9, 100.0, "change_5") is False  # +4.9%
    assert _check_alert_condition(95.0, 100.0, "change_5") is True  # -5% abs
    assert _check_alert_condition(102.0, 100.0, "any") is True  # +2% boundary
    assert _check_alert_condition(101.9, 100.0, "any") is False


def test_alert_condition_rejects_invalid_prices_and_directions() -> None:
    assert _check_alert_condition(0, 100.0, "above") is False
    assert _check_alert_condition(-5.0, 100.0, "above") is False
    assert _check_alert_condition(100.0, 0, "above") is False
    assert _check_alert_condition(105.0, 100.0, "sideways") is False


def test_monitor_baseline_projects_scan_fields_only() -> None:
    baseline = _monitor_baseline(
        {"buy_tax": 0.05, "sell_tax": 0.05, "mintable": True, "extra": "ignored"}
    )
    assert baseline == {
        "buy_tax": 0.05,
        "sell_tax": 0.05,
        "owner_renounced": None,
        "mintable": True,
        "lp_locked": None,
        "honeypot": None,
    }


def test_pct_formats_fraction_or_unknown() -> None:
    assert _pct(0.05) == "5.0%"
    assert _pct(0.123) == "12.3%"
    assert _pct(None) == "unknown"
    assert _pct("junk") == "unknown"


def test_monitor_deltas_detects_tax_drift_bool_flips_lp_drop() -> None:
    baseline = {
        "buy_tax": 0.05,
        "sell_tax": 0.05,
        "owner_renounced": True,
        "mintable": False,
        "lp_locked": 0.9,
        "honeypot": False,
    }
    current = {
        "buy_tax": 0.10,  # +5pp drift
        "sell_tax": 0.055,  # <1pp — ignored
        "owner_renounced": False,  # flip
        "mintable": False,  # unchanged
        "lp_locked": 0.6,  # -30pp drop
        "honeypot": True,  # flip
    }
    assert _monitor_deltas(baseline, current) == [
        "Buy Tax: 5.0% → 10.0%",
        "Ownership renounced: True → False",
        "Honeypot: False → True",
        "LP locked: 90.0% → 60.0%",
    ]


def test_monitor_deltas_ignore_missing_sides_and_small_changes() -> None:
    baseline = {"buy_tax": 0.05, "mintable": True}
    current = {"buy_tax": 0.055, "lp_locked": 0.85}  # mintable side missing
    assert _monitor_deltas(baseline, current) == []
    # LP drop below the 20pp threshold is not flagged.
    assert _monitor_deltas({"lp_locked": 0.9}, {"lp_locked": 0.75}) == []


# ── daily_context: formatters ────────────────────────────────────────────────


def test_fmt_price_tiers() -> None:
    assert _fmt_price(67123) == "$67,123"
    # >=1000 renders with ZERO decimals (docstring's "$3,512.45" is stale).
    assert _fmt_price(3512.45) == "$3,512"
    assert _fmt_price(3.5) == "$3.50"
    assert _fmt_price(0.00421) == "$0.0042"
    assert _fmt_price("junk") == "n/a"
    assert _fmt_price(None) == "n/a"


def test_fmt_mcap_trillions_and_compact_fallback() -> None:
    assert _fmt_mcap(3.2e12) == "$3.20T"
    assert _fmt_mcap(850e6) == "$850.00M"  # falls through to fmt_number
    assert _fmt_mcap("junk") == "n/a"


def test_trending_label_full_render() -> None:
    addr = "0x" + "ab" * 20
    label = _trending_label(
        {
            "name": "Pepe",
            "symbol": "pepe",
            "change_24h": 12.34,
            "chain": "ETH",
            "token_address": addr,
        }
    )
    assert label == f"Pepe (PEPE) +12.3% [eth] — <code>{addr}</code>"


def test_trending_label_skips_coin_id_addresses() -> None:
    label = _trending_label(
        {"name": "Thinking Cat", "symbol": "CAT", "chain": "Solana", "address": "thinking-cat-coin"}
    )
    assert label == "Thinking Cat (CAT) [solana]"  # no <code> tail


def test_trending_label_fallbacks() -> None:
    assert _trending_label({}) == "Unknown Token"
    assert _trending_label({"address": "0xabcdef123456"}) == "0xabcdef1234..."
    assert _trending_label({"symbol": "doge"}) == "DOGE"
    assert _trending_label({"name": "Dogecoin", "change_24h": None}) == "Dogecoin"


def test_launch_label_always_appends_address() -> None:
    label = _launch_label(
        {"name": "Foo", "symbol": "FOO", "chain": "Base", "address": "not-an-address"}
    )
    assert label == "Foo (FOO) [base] — <code>not-an-address</code>"
    assert _launch_label({}) == "Unknown Token"


def test_has_content_requires_any_real_section() -> None:
    assert _has_content({}) is False
    assert _has_content({"prices": {}}) is False
    assert _has_content({"news": []}) is False  # empty list is not content
    assert _has_content({"prices": {"btc": 50000.0}}) is True
    assert _has_content({"total_mcap": 1}) is True
    assert _has_content({"fear_greed_value": 0}) is True  # 0 is a real value
    assert _has_content({"trending": ["x"]}) is True
