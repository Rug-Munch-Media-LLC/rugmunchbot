#!/usr/bin/env python3
"""
Technical analysis indicators for RugMunchBot /ta.

Pure functions computed from OHLCV candles returned by the backend RugCharts
engine (``GET /api/v1/rugcharts/ohlcv/{chain}/{pair}``). No network calls,
no Telegram imports — unit-testable in isolation.

Candle schema (matching ``app/routers/rugcharts_helpers.py``):
    ``{"time": int, "open": float, "high": float, "low": float,
      "close": float, "volume": float}``  (chronological, oldest → newest)

Indicators implemented:
    - RSI-14 (Wilder's smoothing)
    - EMA-9 / EMA-21 (with crossover detection)
    - MACD (12/26/9) with signal + histogram
    - Volume trend (last vs trailing average)
    - Support / resistance from recent swing highs & lows
"""

from __future__ import annotations

import math
from typing import Any

# ── Core series math ────────────────────────────────────────────────────────


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average, aligned with the input (None for warm-up)."""
    out: list[float | None] = []
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        out.append(running / period if i >= period - 1 else None)
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average (Wilder-style seeding via SMA).

    Returns a list the same length as ``values``; entries before the seed
    are ``None``.
    """
    if period <= 0 or not values:
        return [None] * len(values)
    k = 2.0 / (period + 1)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    # Seed: SMA of the first `period` values.
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Relative Strength Index (Wilder's smoothing).

    Returns ``None`` when there is not enough data to compute a stable
    value (< period + 1 closes).
    """
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    # First average: simple mean of the first `period` deltas.
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder smoothing for the remainder.
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(closes: list[float]) -> dict[str, Any]:
    """MACD (12/26/9). Returns MACD line, signal line and histogram lists.

    Histogram = MACD − signal. Positive histogram ⇒ MACD above signal
    (bullish momentum); negative ⇒ bearish.
    """
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    line: list[float | None] = [
        (a - b) if a is not None and b is not None else None for a, b in zip(ema12, ema26, strict=False)
    ]
    # Signal: EMA-9 of the MACD line (skip None warm-up).
    valid = [v for v in line if v is not None]
    start = len(line) - len(valid)
    signal_raw = ema(valid, 9)
    prefix: list[float | None] = [None] * start
    signal: list[float | None] = prefix + signal_raw
    hist: list[float | None] = [
        (macd_v - sig) if macd_v is not None and sig is not None else None
        for macd_v, sig in zip(line, signal, strict=False)
    ]
    return {"line": line, "signal": signal, "histogram": hist}


# ── Volatility + momentum + pump-risk heuristics ────────────────────────────


def stdev(values: list[float]) -> float:
    """Population standard deviation (returns 0.0 for <2 samples)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return (sum((v - mean) ** 2 for v in values) / n) ** 0.5


def bollinger(closes: list[float], period: int = 20, num_std: float = 2.0) -> dict[str, Any]:
    """Bollinger Bands — SMA(period) ± num_std·stdev, last-bar read.

    Returns the current upper/middle/lower band, ``percent_b`` (where price
    sits inside the band: 0 = lower, 1 = upper, >1 = above upper) and
    ``bandwidth`` (band width vs mid, a volatility proxy). ``None`` fields
    when the sample is too small.
    """
    if len(closes) < 2:
        return {"upper": None, "middle": None, "lower": None, "percent_b": None, "bandwidth": None}
    window = closes[-period:] if len(closes) >= period else closes
    mid = sum(window) / len(window)
    sd = stdev(window)
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    price = closes[-1]
    percent_b = (price - lower) / (upper - lower) if upper > lower else 0.5
    bandwidth = (upper - lower) / mid if mid else 0.0
    return {"upper": upper, "middle": mid, "lower": lower, "percent_b": percent_b, "bandwidth": bandwidth}


def atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Average True Range (Wilder) — realized per-candle volatility.

    Needs at least ``period + 1`` candles; returns ``None`` otherwise.
    True Range = max(high-low, |high-prev_close|, |low-prev_close|),
    seeded with a simple mean then Wilder-smoothed.
    """
    if len(candles) < period + 1:
        return None
    prev_close: float | None = None
    trs: list[float] = []
    for c in candles:
        try:
            high = float(c["high"])
            low = float(c["low"])
            close = float(c["close"])
        except (KeyError, ValueError, TypeError):
            continue
        if prev_close is None:
            prev_close = close
            continue
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    if len(trs) < period:
        return None
    seed = sum(trs[:period]) / period
    prev = seed
    for tr in trs[period:]:
        prev = (prev * (period - 1) + tr) / period
    return prev


def realized_volatility(closes: list[float], window: int = 24) -> float | None:
    """Realized volatility — stdev of log returns over the last ``window``
    closes, expressed as a percent. ``None`` when <2 returns exist."""
    if len(closes) < 3:
        return None
    window = min(window, len(closes) - 1)
    rets: list[float] = []
    for i in range(len(closes) - window, len(closes)):
        prev = closes[i - 1]
        cur = closes[i]
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 2:
        return None
    return stdev(rets) * 100.0


def roc(closes: list[float], periods: int) -> float | None:
    """Rate of change over ``periods`` candles (percent). None if short."""
    if len(closes) < periods + 1:
        return None
    base = closes[-periods - 1]
    if not base:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def momentum_label(roc_3h: float | None, roc_24h: float | None) -> str:
    """Momentum read from short vs day ROC (1h candles): accelerating when
    the 3h leg is sharply positive, fading when sharply negative, otherwise
    trending/sideways by the 24h direction."""
    if roc_3h is None or roc_24h is None:
        return "N/A"
    if roc_3h >= 2.0:
        return "ACCELERATING"
    if roc_3h <= -2.0:
        return "FADING"
    if roc_24h >= 1.0:
        return "RISING"
    if roc_24h <= -1.0:
        return "FALLING"
    return "SIDEWAYS"


def pump_risk(analysis: dict[str, Any]) -> dict[str, Any]:
    """Heuristic pump/dump-risk read from the real indicator digest.

    Transparent point system over LIVE values (never invented): volume
    spike vs trailing avg, 24h ROC speed, RSI extension, price hugging the
    window high. Returns a LOW/MEDIUM/HIGH level with the reasons that
    fired. This is a risk screen, not a guarantee — the tool labels it as
    such so the LLM never presents it as certainty.
    """
    score = 0
    reasons: list[str] = []

    ratio = analysis.get("volume_ratio")
    if ratio is not None:
        if ratio >= 2.0:
            score += 2
            reasons.append(f"volume {ratio:.1f}x trailing average")
        elif ratio >= 1.5:
            score += 1
            reasons.append(f"volume {ratio:.1f}x trailing average")

    roc_24 = analysis.get("roc_24h")
    if roc_24 is not None:
        if roc_24 >= 30:
            score += 2
            reasons.append(f"+{roc_24:.0f}% in 24h")
        elif roc_24 <= -20:
            score += 2
            reasons.append(f"{roc_24:.0f}% in 24h (dump)")
        elif roc_24 >= 10:
            score += 1
            reasons.append(f"+{roc_24:.0f}% in 24h")
        elif roc_24 <= -10:
            score += 1
            reasons.append(f"{roc_24:.0f}% in 24h")

    rsi_val = analysis.get("rsi")
    if rsi_val is not None and rsi_val >= 80:
        score += 1
        reasons.append(f"RSI {rsi_val:.0f} overbought")

    # Extension only counts when price actually ran up — a token sitting
    # flat near its recent high is not "extended".
    price = analysis.get("price")
    high = analysis.get("high")
    if price and high and high > 0 and roc_24 is not None and roc_24 >= 5.0 and (high - price) / high < 0.03:
        score += 1
        reasons.append("price near window high after a run")

    level = "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW"
    return {"level": level, "score": score, "reasons": reasons}


# ── Composite analysis ──────────────────────────────────────────────────────


def _last(values: list[float | None]) -> float | None:
    return next((v for v in reversed(values) if v is not None), None)


def analyze_candles(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compute the full TA digest from chronological OHLCV candles.

    Returns ``None`` when there is no usable data, otherwise a dict with
    trend / RSI / EMA / MACD / volume / support-resistance fields (values
    may be ``None`` when the sample is too small for a given indicator).
    """
    if not candles:
        return None
    try:
        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        vols = [float(c.get("volume") or 0) for c in candles]
    except (KeyError, ValueError, TypeError):
        return None
    if not closes:
        return None

    price = closes[-1]
    first = closes[0]
    change_pct = ((price - first) / first * 100) if first else 0.0

    # EMA-9 / EMA-21
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    ema9 = _last(e9)
    ema21 = _last(e21)

    # Trend: EMA structure (9 vs 21) with neutral tolerance.
    if ema9 is not None and ema21 is not None:
        if ema9 > ema21 * 1.001:
            trend = "BULLISH"
        elif ema9 < ema21 * 0.999:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
    else:
        # Fall back to price-vs-window direction when EMAs are warm.
        trend = "BULLISH" if price > first else "BEARISH" if price < first else "NEUTRAL"

    # EMA cross status (compare last two available EMA-9 values vs EMA-21).
    ema_cross = "NEUTRAL"
    if ema9 is not None and ema21 is not None:
        ema_cross = "BULLISH CROSS" if ema9 > ema21 else "BEARISH CROSS"

    # RSI-14
    rsi_val = rsi(closes, 14)
    if rsi_val is not None:
        if rsi_val >= 70:
            rsi_label = "Overbought"
        elif rsi_val <= 30:
            rsi_label = "Oversold"
        else:
            rsi_label = "Neutral"
    else:
        rsi_label = "N/A"

    # MACD
    m = macd(closes)
    macd_line = _last(m["line"])
    macd_signal = _last(m["signal"])
    macd_hist = _last(m["histogram"])
    if macd_hist is not None:
        macd_label = "Bullish" if macd_hist > 0 else "Bearish" if macd_hist < 0 else "Flat"
    else:
        macd_label = "N/A"

    # Volume trend: last vs trailing 20-candle average (min 5 to compare).
    volume_ratio: float | None = None
    vol_trend = "Steady"
    if len(vols) >= 6 and sum(vols[-21:-1]) > 0:
        trailing = sum(vols[-21:-1]) / min(20, len(vols) - 1)
        if trailing > 0:
            volume_ratio = vols[-1] / trailing
            if volume_ratio >= 1.5:
                vol_trend = "Rising"
            elif volume_ratio <= 0.5:
                vol_trend = "Declining"

    # Support / resistance from recent window (default last 20, clamp to sample).
    window = min(20, len(closes))
    resistance = max(highs[-window:])
    support = min(lows[-window:])

    # SMA-20 + Bollinger bands (volatility envelope).
    sma20 = _last(sma(closes, 20))
    bands = bollinger(closes)

    # ATR (per-candle true-range volatility) + realized volatility (%).
    atr_14 = atr(candles, 14)
    atr_pct = (atr_14 / price * 100.0) if atr_14 is not None and price else None
    vol = realized_volatility(closes)

    # Momentum (rate of change over 3 / 12 / 24 1h candles).
    roc_3h = roc(closes, 3)
    roc_12h = roc(closes, 12)
    roc_24h = roc(closes, 24)
    mom = momentum_label(roc_3h, roc_24h)

    digest = {
        "candles": len(candles),
        "price": price,
        "change_pct": change_pct,
        "high": max(highs),
        "low": min(lows),
        "trend": trend,
        "ema9": ema9,
        "ema21": ema21,
        "ema_cross": ema_cross,
        "rsi": rsi_val,
        "rsi_label": rsi_label,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "macd_label": macd_label,
        "volume_ratio": volume_ratio,
        "volume_trend": vol_trend,
        "support": support,
        "resistance": resistance,
        "sma_20": sma20,
        "bollinger_upper": bands.get("upper"),
        "bollinger_middle": bands.get("middle"),
        "bollinger_lower": bands.get("lower"),
        "bollinger_percent_b": bands.get("percent_b"),
        "bollinger_bandwidth": bands.get("bandwidth"),
        "atr_14": atr_14,
        "atr_pct": atr_pct,
        "volatility_pct": vol,
        "roc_3h": roc_3h,
        "roc_12h": roc_12h,
        "roc_24h": roc_24h,
        "momentum": mom,
    }
    digest["pump_risk"] = pump_risk(digest)
    return digest


__all__ = [
    "sma",
    "ema",
    "rsi",
    "macd",
    "stdev",
    "bollinger",
    "atr",
    "realized_volatility",
    "roc",
    "momentum_label",
    "pump_risk",
    "analyze_candles",
]
