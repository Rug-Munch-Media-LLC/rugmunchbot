"""ta.py split part: _prediction_read, _summary_text."""
from __future__ import annotations
from typing import Any

def _prediction_read(analysis: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Honest short-horizon leaning read from the real indicator digest.

    Combines the rule-of-thumb ``verdict`` bias with momentum direction and
    the pump-risk screen. The read is explicitly labelled heuristic and
    never a guarantee: the LLM is expected to present it as "leaning" with
    reasons, not as a forecast. Confidence is data-availability driven
    (candle count + how many signals agree).
    """
    bias = verdict.get("bias", "neutral")
    momentum = analysis.get("momentum") or "N/A"
    pump = analysis.get("pump_risk") or {}

    drivers: list[str] = list(verdict.get("reasons") or [])
    if momentum not in ("N/A",):
        drivers.append(f"momentum {momentum}")
    if pump.get("level") and pump["level"] != "LOW":
        drivers.append(f"pump-risk {pump['level']}")

    # Direction disagreement knocks the lean toward neutral; extreme
    # pump-risk stays flagged even when the bias is bullish.
    lean = bias
    if (bias == "bullish" and momentum in ("FALLING", "FADING")) or (
        bias == "bearish" and momentum in ("RISING", "ACCELERATING")
    ):
        lean = "neutral"
    if pump.get("level") == "HIGH" and lean == "bullish":
        lean = "neutral"

    n = analysis.get("candles") or 0
    if n < 30:
        confidence = "low"
    elif n < 60 or lean == "neutral" or pump.get("level") == "HIGH":
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "leaning": lean,
        "confidence": confidence,
        "horizon": "next 12-24h (1h candles)",
        "key_drivers": drivers[:6],
        "pump_risk": {"level": pump.get("level", "LOW"), "score": pump.get("score", 0)},
        "note": "heuristic read from live indicators — not financial advice, not a guarantee",
    }

def _summary_text(analysis: dict[str, Any]) -> str:
    """Compact one-line metric digest (chat-friendly; the LLM renders it)."""
    rsi_value = analysis.get("rsi")
    rsi_part = (
        f"RSI-14 {rsi_value:.1f} ({analysis.get('rsi_label') or 'n/a'})"
        if rsi_value is not None
        else "RSI-14 n/a"
    )

    ema9 = analysis.get("ema9")
    ema21 = analysis.get("ema21")
    ema_part = (
        f"EMA9 {ema9:.6g} / EMA21 {ema21:.6g}"
        if ema9 is not None and ema21 is not None
        else "EMA n/a"
    )

    macd_part = f"MACD {analysis.get('macd_label') or 'n/a'}"

    support = analysis.get("support")
    resistance = analysis.get("resistance")
    sr_part = (
        f"S/R {support:.6g}/{resistance:.6g}"
        if support is not None and resistance is not None
        else "S/R n/a"
    )

    vol_part = (
        f"vol {analysis.get('volatility_pct'):.1f}%"
        if analysis.get("volatility_pct") is not None
        else "vol n/a"
    )
    mom_part = f"mom {analysis.get('momentum') or 'n/a'}"
    pump = analysis.get("pump_risk") or {}
    pump_part = f"pump-risk {pump.get('level', 'n/a')}"

    return (
        f"{rsi_part} · {ema_part} · {macd_part} · {sr_part} · "
        f"trend {analysis.get('trend') or 'NEUTRAL'} · {vol_part} · {mom_part} · {pump_part}"
    )
