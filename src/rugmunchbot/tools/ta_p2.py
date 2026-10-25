"""ta.py split part: _backend_candles, _gecko_candles, _fetch_candles, _verdict."""
from __future__ import annotations
from rugmunchbot.data_resolver import resolve_with_fallback
from rugmunchbot.tools._client import BackendApiError, http_get_json
from rugmunchbot.tools.registry import backend_json_get
from typing import Any
from .ta_p1 import _Candle, _CandleResult, _GECKO_NETWORKS, _GECKO_OHLCV_URL, _normalize_chain, logger  # noqa: F401

async def _backend_candles(chain: str, pair_address: str) -> _CandleResult:
    """Own-data candles via the backend RugCharts engine (ClickHouse first,
    GeckoTerminal server-side fallback). Returns (candles, source_label)."""
    body: dict[str, Any] | None
    try:
        body = await backend_json_get(
            f"/api/v1/rugcharts/ohlcv/{chain}/{pair_address}",
            params={"timeframe": "1h", "limit": 100},
            timeout=6.0,
        )
    except BackendApiError as exc:
        # Own-data leg of the candle chain (GeckoTerminal fallback follows):
        # a dead backend degrades to a miss, not a tool-level failure.
        logger.debug("ta candles backend miss (%s): %s", exc.status_code, exc)
        return [], ""
    if not isinstance(body, dict):
        return [], ""
    data = body.get("data")
    candles = data.get("candles") if isinstance(data, dict) else None
    if isinstance(candles, list) and candles:
        return candles, f"backend:/api/v1/rugcharts/ohlcv/{chain}/{pair_address}"
    return [], ""

async def _gecko_candles(chain: str, pair_address: str) -> _CandleResult:
    """GeckoTerminal direct OHLCV (same query pattern as the backend helper
    ``app/routers/rugcharts_helpers._fetch_ohlcv_gecko``)."""
    network = _GECKO_NETWORKS.get(_normalize_chain(chain), _normalize_chain(chain))
    body = await http_get_json(
        _GECKO_OHLCV_URL.format(network=network, pair=pair_address),
        params={"aggregate": 1, "limit": 100, "currency": "usd"},
        timeout=6.0,
    )
    if not isinstance(body, dict):
        return [], ""
    attrs = body.get("data", {}).get("attributes") if isinstance(body.get("data"), dict) else None
    ohlcv_list = attrs.get("ohlcv_list") if isinstance(attrs, dict) else None
    if not isinstance(ohlcv_list, list):
        return [], ""
    candles: list[_Candle] = []
    for c in reversed(ohlcv_list):  # source is newest-first; we want chronological
        if not isinstance(c, list) or len(c) < 6:
            continue
        try:
            candles.append(
                {
                    "time": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
            )
        except (TypeError, ValueError):
            continue
    if candles:
        return candles, f"geckoterminal:direct:{network}:{pair_address}"
    return [], ""

async def _fetch_candles(chain: str, pair_address: str) -> _CandleResult:
    """Run the own-data-first candle cascade (backend -> GeckoTerminal)."""
    try:
        return await resolve_with_fallback(
            f"ta_ohlcv:{chain}:{pair_address[:10]}",
            lambda: _backend_candles(chain, pair_address),
            lambda: _gecko_candles(chain, pair_address),
            timeout_secs=5.0,
            is_valid=lambda res: bool(res and isinstance(res[0], list) and len(res[0]) >= 2),
        )
    except Exception as exc:  # pragma: no cover — both sources failed
        logger.debug("ta_ohlcv both sources failed: %s", exc)
        return [], ""

def _verdict(analysis: dict[str, Any]) -> dict[str, Any]:
    """Transparent rule-of-thumb bias from the real indicator digest.

    Scores EMA structure (+/-2), EMA cross (+/-1), MACD (+/-1) and RSI
    tilt (+/-1); ``bullish`` when score >= 2, ``bearish`` when <= -2,
    ``neutral`` otherwise. Overbought/oversold RSI is reported as risk in
    the reasons instead of contributing to the tilt.
    """
    score = 0.0
    reasons: list[str] = []

    trend = analysis.get("trend") or "NEUTRAL"
    if trend == "BULLISH":
        score += 2
        reasons.append("EMA-9 above EMA-21")
    elif trend == "BEARISH":
        score -= 2
        reasons.append("EMA-9 below EMA-21")
    else:
        reasons.append("EMAs flat")

    cross = analysis.get("ema_cross")
    if cross == "BULLISH CROSS":
        score += 1
        reasons.append("EMA bullish cross")
    elif cross == "BEARISH CROSS":
        score -= 1
        reasons.append("EMA bearish cross")

    macd_label = analysis.get("macd_label")
    if macd_label == "Bullish":
        score += 1
        reasons.append("MACD above signal")
    elif macd_label == "Bearish":
        score -= 1
        reasons.append("MACD below signal")

    rsi_value = analysis.get("rsi")
    if rsi_value is not None:
        if rsi_value >= 70:
            reasons.append(f"RSI {rsi_value:.1f} overbought (extension risk)")
        elif rsi_value <= 30:
            reasons.append(f"RSI {rsi_value:.1f} oversold (bounce risk)")
        elif rsi_value > 55:
            score += 1
            reasons.append(f"RSI {rsi_value:.1f} firm")
        elif rsi_value < 45:
            score -= 1
            reasons.append(f"RSI {rsi_value:.1f} soft")
        else:
            reasons.append(f"RSI {rsi_value:.1f} neutral")

    bias = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"

    # Confidence: how many independent signals agree with the bias.
    # Max raw score is ±5 (EMA ±2, cross ±1, MACD ±1, RSI ±1).
    # Agreement = |score| / 5 → 0% (all disagree) to 100% (max conviction).
    max_score = 5.0
    agreement = min(abs(score) / max_score, 1.0)
    n_signals = len([r for r in reasons if "flat" not in r.lower() and "neutral" not in r.lower()])
    confidence = round(agreement * 60 + min(n_signals, 4) / 4 * 40, 0)
    confidence = int(min(confidence, 95))  # never 100% — always uncertainty

    conviction = (
        "STRONG" if confidence >= 75 and abs(score) >= 3
        else "MODERATE" if confidence >= 50
        else "WEAK"
    )

    lead = reasons[0] if reasons else "no signal"
    one_liner = f"{bias.upper()} (score {score:+.0f}, {conviction.lower()} conviction) — {lead}"
    return {
        "bias": bias,
        "score": round(score, 1),
        "confidence": confidence,
        "conviction": conviction,
        "n_signals": n_signals,
        "reasons": reasons,
        "one_liner": one_liner,
    }
