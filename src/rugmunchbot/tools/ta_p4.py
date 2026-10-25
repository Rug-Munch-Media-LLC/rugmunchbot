"""ta.py split part: technical_analysis, register_tool."""
from __future__ import annotations
from datetime import UTC, datetime
from rugmunchbot.ta_indicators import analyze_candles
from rugmunchbot.tools.registry import ToolResult
from typing import Any
from .ta_p1 import _market_context, _normalize_chain, _pair_snapshot, _resolve_pair  # noqa: F401
from .ta_p2 import _fetch_candles, _verdict  # noqa: F401
from .ta_p3 import _prediction_read, _summary_text  # noqa: F401

async def technical_analysis(*, address: str = "", symbol: str = "", chain: str = "") -> ToolResult:
    """Real technical analysis for a token address or ``$SYMBOL``.

    Resolves the pair (DexScreener), fetches 1h OHLCV own-data first
    (backend RugCharts/ClickHouse -> GeckoTerminal), then computes RSI-14,
    EMA-9/21, MACD, support/resistance and a trend verdict with the reused
    pure-Python ``ta_indicators`` math.

    Args:
        address: Token contract address (EVM or Solana).
        symbol: ``$SYMBOL`` (used when no address is given).
        chain: Optional chain filter ("ethereum", "solana", "base", ...).

    Returns:
        ToolResult with structured indicators + verdict + a compact
        ``summary_text`` line. ``ok=False`` only when no pair can be
        resolved. When candles are unavailable the tool still returns
        ``ok=True`` with the REAL DexScreener snapshot and
        ``indicators_available=false`` — indicators are never fabricated.
    """
    pair = await _resolve_pair(address, symbol, chain)
    if pair is None:
        return ToolResult(
            tool="technical_analysis",
            ok=False,
            error="could not resolve a trading pair for that symbol/address (DexScreener unreachable or no match)",
        )

    resolved_chain = _normalize_chain(chain or str(pair.get("chainId") or ""))
    pair_address = str(pair.get("pairAddress") or "")
    snapshot = _pair_snapshot(pair, address, symbol)

    candles, source = await _fetch_candles(resolved_chain, pair_address)

    if candles:
        analysis = analyze_candles(candles)
        if analysis is None:
            return ToolResult(
                tool="technical_analysis",
                ok=False,
                error="OHLCV candles returned but could not be parsed (malformed payload)",
            )
        verdict = _verdict(analysis)
        indicators = {
            "price": analysis.get("price"),
            "change_pct": analysis.get("change_pct"),
            "candles": analysis.get("candles"),
            "rsi_14": analysis.get("rsi"),
            "rsi_label": analysis.get("rsi_label"),
            "ema_9": analysis.get("ema9"),
            "ema_21": analysis.get("ema21"),
            "ema_cross": analysis.get("ema_cross"),
            "macd_line": analysis.get("macd_line"),
            "macd_signal": analysis.get("macd_signal"),
            "macd_hist": analysis.get("macd_hist"),
            "macd_label": analysis.get("macd_label"),
            "support": analysis.get("support"),
            "resistance": analysis.get("resistance"),
            "sma_20": analysis.get("sma_20"),
            "bollinger_upper": analysis.get("bollinger_upper"),
            "bollinger_middle": analysis.get("bollinger_middle"),
            "bollinger_lower": analysis.get("bollinger_lower"),
            "bollinger_percent_b": analysis.get("bollinger_percent_b"),
            "bollinger_bandwidth": analysis.get("bollinger_bandwidth"),
            "atr_14": analysis.get("atr_14"),
            "atr_pct": analysis.get("atr_pct"),
            "volatility_pct": analysis.get("volatility_pct"),
            "roc_3h": analysis.get("roc_3h"),
            "roc_12h": analysis.get("roc_12h"),
            "roc_24h": analysis.get("roc_24h"),
            "momentum": analysis.get("momentum"),
            "pump_risk": analysis.get("pump_risk"),
            "trend": analysis.get("trend"),
            "volume_ratio": analysis.get("volume_ratio"),
            "volume_trend": analysis.get("volume_trend"),
        }
        return ToolResult(
            tool="technical_analysis",
            ok=True,
            data={
                "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
                "symbol": snapshot["symbol"],
                "name": snapshot["name"],
                "chain": resolved_chain,
                "pair_address": pair_address,
                "timeframe": "1h",
                "indicators_available": True,
                "indicators": indicators,
                "market": _market_context(snapshot, analysis),
                "verdict": verdict,
                "prediction": _prediction_read(analysis, verdict),
                "summary_text": _summary_text(analysis),
                "updated_at": datetime.now(UTC).isoformat(),
            },
            symbols=[snapshot["symbol"]] if snapshot["symbol"] else [],
            addresses=[snapshot["address"]] if snapshot["address"] else [],
            source=source or "ohlcv:unknown",
        )

    # Honest degraded path: no candle source reachable. DexScreener exposes
    # no OHLCV, so only the real snapshot metrics are returned.
    momentum: list[str] = []
    if snapshot["change_1h"] is not None:
        momentum.append(f"1h {snapshot['change_1h']:+.2f}%")
    if snapshot["change_6h"] is not None:
        momentum.append(f"6h {snapshot['change_6h']:+.2f}%")
    if snapshot["change_24h"] is not None:
        momentum.append(f"24h {snapshot['change_24h']:+.2f}%")
    momentum_text = " · ".join(momentum) if momentum else "no change data"
    one_liner = f"INSUFFICIENT DATA (score 0) — no OHLCV candles right now; live snapshot only: {momentum_text}"
    return ToolResult(
        tool="technical_analysis",
        ok=True,
        data={
            "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
            "symbol": snapshot["symbol"],
            "name": snapshot["name"],
            "chain": resolved_chain,
            "pair_address": pair_address,
            "timeframe": "1h",
            "indicators_available": False,
            "indicators": None,
            "snapshot": snapshot,
            "market": _market_context(snapshot, None),
            "verdict": {
                "bias": "insufficient_data",
                "score": 0.0,
                "reasons": [
                    "backend RugCharts (ClickHouse + GeckoTerminal) returned no candles; "
                    "DexScreener exposes no OHLCV history"
                ],
                "one_liner": one_liner,
            },
            "prediction": {
                "leaning": "insufficient_data",
                "confidence": "low",
                "horizon": "next 12-24h (1h candles)",
                "key_drivers": ["no OHLCV candle history to base a read on"],
                "note": "heuristic read from live indicators — not financial advice, not a guarantee",
            },
            "summary_text": f"No OHLCV candle history available right now — live snapshot: {momentum_text}",
            "updated_at": datetime.now(UTC).isoformat(),
        },
        symbols=[snapshot["symbol"]] if snapshot["symbol"] else [],
        addresses=[snapshot["address"]] if snapshot["address"] else [],
        source="dexscreener:snapshot (no candles)",
    )

def register_tool(reg: Any) -> None:
    """Register this tool on the shared registry (see trending_engine for notes)."""
    reg.register("technical_analysis", technical_analysis, timeout=12.0, ttl=120.0)
