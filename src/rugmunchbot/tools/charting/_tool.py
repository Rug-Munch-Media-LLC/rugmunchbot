"""chart tool handler — ASCII price-action chart + TA digest for a token.

Extracted verbatim from the former single-module ``tools/charting.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.ta_indicators import analyze_candles
from rugmunchbot.tools.charting._candles import _fetch_candles
from rugmunchbot.tools.charting._constants import _TIMEFRAMES
from rugmunchbot.tools.charting._pairs import (
    _normalize_chain,
    _pair_snapshot,
    _resolve_pair,
)
from rugmunchbot.tools.charting._render import (
    _bias,
    _candle_direction_row,
    _render_chart,
    _slice_candles,
    _sparkline,
    _summary_text,
)
from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.tools.charting")


async def chart(
    *,
    address: str = "",
    symbol: str = "",
    chain: str = "",
    timeframe: str = "24h",
    width: int = 48,
    height: int = 6,
) -> ToolResult:
    """Compact ASCII price-action chart + TA digest for a token address or
    ``$SYMBOL``.

    Resolves the pair (DexScreener), fetches OHLCV own-data first (backend
    RugCharts/ClickHouse -> GeckoTerminal), slices to the requested window
    (last 24 hourly candles for ``24h``, last 7 daily for ``7d``) and renders
    a text-only chart: ASCII sparkline, per-candle direction row, vertical
    price ladder and the TA digest (RSI-14 / EMA-9/21 / MACD /
    support-resistance) from the reused pure ``ta_indicators`` math.

    Args:
        address: Token contract address (EVM or Solana).
        symbol: ``$SYMBOL`` (used when no address is given).
        chain: Optional chain filter ("ethereum", "solana", "base", ...).
        timeframe: "24h" (24 hourly candles) or "7d" (7 daily candles).
        width: Max sparkline / ladder character width.
        height: Ladder rows.

    Returns:
        ToolResult with ``chart_text`` (the monospace block), ``sparkline``,
        ``candle_directions``, the indicator digest and a ``summary_text``.
        ``ok=False`` only when no pair can be resolved. When no candles are
        reachable the tool still returns ``ok=True`` with the REAL DexScreener
        snapshot and ``indicators_available=false`` — the chart is never
        fabricated.
    """
    tf = timeframe.strip().lower()
    if tf not in _TIMEFRAMES:
        tf = "24h"
    unit = _TIMEFRAMES[tf][0]
    gecko_unit = _TIMEFRAMES[tf][1]

    pair = await _resolve_pair(address, symbol, chain)
    if pair is None:
        return ToolResult(
            tool="chart",
            ok=False,
            error="could not resolve a trading pair for that symbol/address (DexScreener unreachable or no match)",
        )

    resolved_chain = _normalize_chain(chain or str(pair.get("chainId") or ""))
    pair_address = str(pair.get("pairAddress") or "")
    snapshot = _pair_snapshot(pair, address, symbol)

    candles, source = await _fetch_candles(resolved_chain, pair_address, unit, gecko_unit)
    candles = _slice_candles(candles, tf)

    if candles:
        analysis = analyze_candles(candles)
        if analysis is None:
            return ToolResult(
                tool="chart",
                ok=False,
                error="OHLCV candles returned but could not be parsed (malformed payload)",
            )
        closes = [float(c["close"]) for c in candles]
        chart_text = _render_chart(
            snapshot, tf, unit, candles, analysis, closes, source, width, height
        )
        return ToolResult(
            tool="chart",
            ok=True,
            data={
                "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
                "symbol": snapshot["symbol"],
                "name": snapshot["name"],
                "chain": resolved_chain,
                "pair_address": pair_address,
                "timeframe": tf,
                "candle_unit": unit,
                "candles_shown": len(candles),
                "indicators_available": True,
                "chart_text": chart_text,
                "sparkline": _sparkline(closes, width),
                "candle_directions": _candle_direction_row(candles),
                "indicators": {
                    "price": analysis.get("price"),
                    "change_pct": analysis.get("change_pct"),
                    "rsi_14": analysis.get("rsi"),
                    "ema_9": analysis.get("ema9"),
                    "ema_21": analysis.get("ema21"),
                    "macd_label": analysis.get("macd_label"),
                    "support": analysis.get("support"),
                    "resistance": analysis.get("resistance"),
                    "trend": analysis.get("trend"),
                },
                "verdict": {"bias": _bias(analysis)},
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
    return ToolResult(
        tool="chart",
        ok=True,
        data={
            "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
            "symbol": snapshot["symbol"],
            "name": snapshot["name"],
            "chain": resolved_chain,
            "pair_address": pair_address,
            "timeframe": tf,
            "candle_unit": unit,
            "candles_shown": 0,
            "indicators_available": False,
            "chart_text": f"No OHLCV candle history available right now — live snapshot: {momentum_text}",
            "sparkline": "",
            "candle_directions": "",
            "indicators": None,
            "snapshot": snapshot,
            "summary_text": f"No OHLCV candle history available right now — live snapshot: {momentum_text}",
            "updated_at": datetime.now(UTC).isoformat(),
        },
        symbols=[snapshot["symbol"]] if snapshot["symbol"] else [],
        addresses=[snapshot["address"]] if snapshot["address"] else [],
        source="dexscreener:snapshot (no candles)",
    )


# ── Registration (self-owned; invoked by tools/__init__._register_defaults) ─


def register_tool(reg: Any) -> None:
    """Register this tool on the shared registry (see trend_algo for notes)."""
    reg.register("chart", chart, timeout=12.0, ttl=120.0)
