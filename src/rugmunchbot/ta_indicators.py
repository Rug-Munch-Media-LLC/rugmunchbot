#!/usr/bin/env python3
"""
Technical analysis indicators for RugMunchBot /ta.

Pure functions computed from OHLCV candles returned by the backend RugCharts
engine (``GET /api/v1/rugcharts/ohlcv/{chain}/{pair}``). No network calls,
no Telegram imports — unit-testable in isolation.

Candle schema (matching ``app/routers/rugcharts_helpers.py``):
    ``{"time": int, "open": float, "high": float, "low": float,
      "close": float, "volume": float}``  (chronological, oldest -> newest)

Indicators implemented:
    - RSI-14 (Wilder's smoothing)
    - EMA-9 / EMA-21 (with crossover detection)
    - MACD (12/26/9) with signal + histogram
    - Volume trend (last vs trailing average)
    - Support / resistance from recent swing highs & lows
"""

from __future__ import annotations

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

    Histogram = MACD - signal. Positive histogram ⇒ MACD above signal
    (bullish momentum); negative ⇒ bearish.
    """
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    line: list[float | None] = [
        (a - b) if a is not None and b is not None else None
        for a, b in zip(ema12, ema26, strict=False)
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

    return {
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
    }


__all__ = ["analyze_candles", "ema", "macd", "rsi", "sma"]
