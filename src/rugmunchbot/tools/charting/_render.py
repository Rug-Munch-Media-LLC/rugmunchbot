"""chart ASCII rendering — sparkline, direction row, ladder, TA digest lines.

Extracted verbatim from the former single-module ``tools/charting.py``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.tools.charting._constants import _SPARK_CHARS, _TIMEFRAMES, _Candle
from rugmunchbot.tools.charting._pairs import _to_float


def _slice_candles(candles: list[_Candle], timeframe: str) -> list[_Candle]:
    """Last N candles for the requested timeframe (24 hourly / 7 daily)."""
    n = _TIMEFRAMES.get(timeframe, ("1h", "hour", 24))[2]
    return candles[-n:] if candles else []


# ── ASCII rendering (pure functions; unit-testable) ────────────────────────


def _sparkline(closes: list[float], width: int = 48) -> str:
    """One-line ASCII sparkline of the close series (normalized to the
    observed min/max). ``width`` caps the character count (even sampling)."""
    if not closes:
        return ""
    lo = min(closes)
    hi = max(closes)
    if len(closes) <= width:
        sampled = closes
    else:
        idxs = [round(i * (len(closes) - 1) / (width - 1)) for i in range(width)]
        sampled = [closes[i] for i in idxs]
    span = hi - lo
    out: list[str] = []
    for v in sampled:
        level = (
            len(_SPARK_CHARS) // 2
            if span < 1e-12
            else round((v - lo) / span * (len(_SPARK_CHARS) - 1))
        )
        out.append(_SPARK_CHARS[max(0, min(level, len(_SPARK_CHARS) - 1))])
    return "".join(out)


def _candle_direction_row(candles: list[_Candle]) -> str:
    """Per-candle up/down row: '+' up, '-' down, '=' flat, '.' unknown."""
    out: list[str] = []
    for c in candles:
        o = _to_float(c.get("open"))
        cl = _to_float(c.get("close"))
        if o is None or cl is None:
            out.append(".")
            continue
        if cl > o:
            out.append("+")
        elif cl < o:
            out.append("-")
        else:
            out.append("=")
    return "".join(out)


def _price_ladder(closes: list[float], width: int = 40, height: int = 6) -> str:
    """Compact vertical ASCII chart: rows of '.' with '*' at sampled closes.

    Y-axis labels at the top (high) and bottom (low) of the observed range.
    """
    if not closes:
        return ""
    lo = min(closes)
    hi = max(closes)
    span = hi - lo
    rows: list[str] = []
    for row in range(height):
        level_hi = hi - span * (row / height)
        level_lo = hi - span * ((row + 1) / height)
        line: list[str] = []
        for i, v in enumerate(closes):
            if i >= width:
                break
            line.append("*" if level_lo <= v <= level_hi else ".")
        if row == 0:
            label = f"{hi:.8g}"
        elif row == height - 1:
            label = f"{lo:.8g}"
        else:
            label = ""
        rows.append(f"{label:>12} {''.join(line)}")
    return "\n".join(rows)


def _summary_text(analysis: dict[str, Any]) -> str:
    """Compact one-line metric digest (chat-friendly; the LLM renders it)."""
    rsi_value = analysis.get("rsi")
    rsi_part = f"RSI-14 {rsi_value:.1f}" if rsi_value is not None else "RSI-14 n/a"
    ema9 = analysis.get("ema9")
    ema21 = analysis.get("ema21")
    ema_part = (
        f"EMA9 {ema9:.6g}/EMA21 {ema21:.6g}"
        if ema9 is not None and ema21 is not None
        else "EMA n/a"
    )
    macd_part = f"MACD {analysis.get('macd_label') or 'n/a'}"
    return f"{rsi_part} · {ema_part} · {macd_part} · trend {analysis.get('trend') or 'NEUTRAL'}"


def _bias(analysis: dict[str, Any]) -> str:
    """Compact transparent bias from the real indicator digest."""
    score = 0
    trend = analysis.get("trend")
    if trend == "BULLISH":
        score += 1
    elif trend == "BEARISH":
        score -= 1
    rsi_value = analysis.get("rsi")
    if rsi_value is not None and rsi_value > 55:
        score += 1
    elif rsi_value is not None and rsi_value < 45:
        score -= 1
    return "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"


def _levels_line(analysis: dict[str, Any]) -> str:
    """Key price levels from the analysis (real candle values)."""
    parts: list[str] = []
    price = analysis.get("price")
    high = analysis.get("high")
    low = analysis.get("low")
    support = analysis.get("support")
    resistance = analysis.get("resistance")
    if price is not None:
        parts.append(f"last {price:.8g}")
    if high is not None:
        parts.append(f"high {high:.8g}")
    if low is not None:
        parts.append(f"low {low:.8g}")
    if support is not None:
        parts.append(f"support {support:.8g}")
    if resistance is not None:
        parts.append(f"resistance {resistance:.8g}")
    return " · ".join(parts)


def _render_chart(
    snapshot: dict[str, Any],
    timeframe: str,
    unit: str,
    candles: list[_Candle],
    analysis: dict[str, Any],
    closes: list[float],
    source: str,
    width: int,
    height: int,
) -> str:
    """Assemble the Telegram-monospace ASCII chart block (text only)."""
    sym = snapshot.get("symbol") or "?"
    chain = snapshot.get("chain") or ""
    header = (
        f"${sym} ({chain}) — {timeframe} chart ({len(candles)} x {unit} candles)  [src: {source}]"
    )
    lines = [
        header,
        _sparkline(closes, width),
        _candle_direction_row(candles),
        _price_ladder(closes, min(width, 40), height),
        _summary_text(analysis),
        _levels_line(analysis),
    ]
    return "\n".join(line for line in lines if line)
