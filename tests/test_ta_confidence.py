"""TA confidence scoring tests."""

from rugmunchbot.tools.ta import _verdict


def test_strong_bullish_high_confidence():
    r = _verdict(
        {"trend": "BULLISH", "ema_cross": "BULLISH CROSS", "macd_label": "Bullish", "rsi": 58}
    )
    assert r["bias"] == "bullish"
    assert r["confidence"] >= 70
    assert r["conviction"] == "STRONG"
    assert r["n_signals"] >= 3


def test_strong_bearish_high_confidence():
    r = _verdict(
        {"trend": "BEARISH", "ema_cross": "BEARISH CROSS", "macd_label": "Bearish", "rsi": 25}
    )
    assert r["bias"] == "bearish"
    assert r["confidence"] >= 60
    assert r["conviction"] in ("STRONG", "MODERATE")


def test_neutral_low_confidence():
    r = _verdict({"trend": "NEUTRAL", "rsi": 50})
    assert r["bias"] == "neutral"
    assert r["confidence"] <= 30
    assert r["conviction"] == "WEAK"


def test_confidence_capped_at_95():
    """Never 100% — always uncertainty."""
    r = _verdict(
        {"trend": "BULLISH", "ema_cross": "BULLISH CROSS", "macd_label": "Bullish", "rsi": 58}
    )
    assert r["confidence"] <= 95


def test_overbought_flagged_as_risk():
    r = _verdict({"trend": "BULLISH", "rsi": 78})
    assert any("overbought" in reason.lower() for reason in r["reasons"])
