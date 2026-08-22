"""Wave 7 — ta_indicators: pure series math + composite digest.

All expectations hand-computed or probed against the implementation before
asserting (oracle policy). Float comparisons use pytest.approx.
"""

from __future__ import annotations

import pytest

from rugmunchbot.ta_indicators import (
    analyze_candles,
    atr,
    bollinger,
    ema,
    macd,
    momentum_label,
    pump_risk,
    realized_volatility,
    roc,
    rsi,
    sma,
    stdev,
)

# ── Core series math ────────────────────────────────────────────────────


def test_sma_aligned_with_warmup_nones() -> None:
    assert sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_sma_seeded() -> None:
    # seed = SMA(1,2,3)=2.0 at idx2; k=0.5 → idx3=3.0, idx4=4.0
    assert ema([1.0, 2.0, 3.0, 4.0, 5.0], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_short_sample_all_none() -> None:
    assert ema([1.0, 2.0], 3) == [None, None]
    assert ema([], 3) == []


def test_rsi_monotonic_up_is_100_flat_is_50() -> None:
    assert rsi([float(i) for i in range(1, 16)]) == 100.0
    assert rsi([5.0] * 15) == 50.0


def test_rsi_insufficient_data_none() -> None:
    assert rsi([1.0, 2.0, 3.0]) is None


def test_stdev_population_semantics() -> None:
    assert stdev([2.0, 4.0]) == pytest.approx(1.0)
    assert stdev([5.0]) == 0.0


# ── Volatility / momentum ───────────────────────────────────────────────


def test_bollinger_constant_series_degenerate_band() -> None:
    bands = bollinger([5.0] * 20)
    assert bands["upper"] == bands["middle"] == bands["lower"] == 5.0
    assert bands["percent_b"] == 0.5  # upper == lower branch
    assert bands["bandwidth"] == 0.0


def test_bollinger_tiny_sample_all_none() -> None:
    assert bollinger([1.0])["upper"] is None


def test_atr_hand_computed() -> None:
    candles = [
        {"high": 10, "low": 5, "close": 7},
        {"high": 12, "low": 6, "close": 11},
        {"high": 11, "low": 8, "close": 9},
    ]
    # TRs: [max(5, |10-7|, |5-7|)=5, max(6, |12-11|, |6-11|)=4] → mean 4.5
    assert atr(candles, 2) == pytest.approx(4.5)


def test_atr_needs_period_plus_one() -> None:
    assert atr([{"high": 1, "low": 1, "close": 1}], 14) is None


def test_realized_volatility_guards() -> None:
    assert realized_volatility([1.0]) is None
    assert realized_volatility([100.0, 110.0]) is None  # <2 returns


def test_roc_percent_and_zero_base_guard() -> None:
    assert roc([100.0, 110.0], 1) == pytest.approx(10.0)
    assert roc([100.0] * 5, 3) == 0.0
    assert roc([100.0], 3) is None
    assert roc([0.0, 5.0], 1) is None  # zero base guard


@pytest.mark.parametrize(
    ("roc3", "roc24", "expected"),
    [
        (None, None, "N/A"),
        (2.0, -50.0, "ACCELERATING"),  # 3h leg wins
        (-2.0, 50.0, "FADING"),
        (0.0, 1.0, "RISING"),
        (0.0, -1.0, "FALLING"),
        (0.0, 0.0, "SIDEWAYS"),
    ],
)
def test_momentum_label_boundaries(roc3: float | None, roc24: float | None, expected: str) -> None:
    assert momentum_label(roc3, roc24) == expected


# ── pump_risk scoring ───────────────────────────────────────────────────


def test_pump_risk_low_when_nothing_fires() -> None:
    out = pump_risk({"volume_ratio": None, "roc_24h": None, "rsi": None})
    assert out == {"level": "LOW", "score": 0, "reasons": []}


def test_pump_risk_volume_spike_medium() -> None:
    out = pump_risk({"volume_ratio": 2.5, "roc_24h": None, "rsi": None})
    assert out["score"] == 2
    assert "volume 2.5x trailing average" in out["reasons"]
    assert out["level"] == "MEDIUM"


def test_pump_risk_dump_scores_like_pump() -> None:
    out = pump_risk({"roc_24h": -25.0})
    assert out["score"] == 2
    assert any("dump" in r for r in out["reasons"])


def test_pump_risk_high_requires_stack() -> None:
    out = pump_risk({"volume_ratio": 2.0, "roc_24h": 35.0, "rsi": 85, "price": 9.9, "high": 10.0})
    assert out["score"] >= 4
    assert out["level"] == "HIGH"


def test_pump_risk_extension_requires_runup() -> None:
    # Flat near high: no extension point without roc_24h >= 5.
    out = pump_risk({"price": 9.9, "high": 10.0, "roc_24h": None})
    assert all("window high" not in r for r in out["reasons"])


# ── Composite digest ────────────────────────────────────────────────────


def test_analyze_candles_rejects_empty_and_bad_rows() -> None:
    assert analyze_candles([]) is None
    assert analyze_candles([{"close": "x"}]) is None
    assert analyze_candles([{"high": 1}]) is None  # missing close


def _uptrend(n: int) -> list[dict[str, float]]:
    return [
        {
            "time": i,
            "open": 100.0 + i,
            "high": 102.0 + i,
            "low": 99.0 + i,
            "close": 101.0 + i,
            "volume": 10.0,
        }
        for i in range(n)
    ]


def test_analyze_candles_uptrend_shape() -> None:
    dig = analyze_candles(_uptrend(30))
    assert dig is not None
    assert dig["trend"] == "BULLISH"
    assert dig["ema_cross"] == "BULLISH CROSS"
    assert dig["momentum"] == "ACCELERATING"
    assert dig["rsi"] is not None
    assert dig["rsi"] > 70
    assert dig["rsi_label"] == "Overbought"
    # Support/resistance come from the last-20 window (30-candle sample →
    # lows[10:] → min low = 99 + 10; highs still peak at the final bar).
    assert dig["support"] == pytest.approx(109.0)
    assert dig["resistance"] == pytest.approx(131.0)
    assert dig["pump_risk"]["level"] in {"LOW", "MEDIUM", "HIGH"}


def test_analyze_candles_downtrend_bearish() -> None:
    candles = list(reversed(_uptrend(30)))
    dig = analyze_candles(candles)
    assert dig is not None
    assert dig["trend"] == "BEARISH"
    assert dig["ema_cross"] == "BEARISH CROSS"


def test_macd_lists_align_with_input_length() -> None:
    closes = [float(x) for x in range(1, 41)]
    m = macd(closes)
    assert len(m["line"]) == len(m["signal"]) == len(m["histogram"]) == len(closes)
    # Histogram identity holds wherever both lines are defined.
    pairs = [(a, b, c) for a, b, c in zip(m["line"], m["signal"], m["histogram"], strict=False)]
    defined = [(a, b, c) for a, b, c in pairs if a is not None and b is not None and c is not None]
    assert defined, "expected some fully-defined MACD bars"
    for a, b, c in defined:
        assert c == pytest.approx(a - b)
