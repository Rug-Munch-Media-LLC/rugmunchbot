"""chart tool payloads — the two ToolResult shapes the handler returns.

Split out of ``charting/_tool.py``: the handler resolves and renders, this
module builds the answer payloads (candles path and the honest snapshot-only
degraded path).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.tools.charting._render import (
    ChartCanvas,
    _bias,
    _candle_direction_row,
    _render_chart,
    _sparkline,
    _summary_text,
)
from rugmunchbot.tools.registry import ToolResult

_NO_CANDLES = "No OHLCV candle history available right now"


@dataclass
class ChartAnswer:
    """Everything both payloads share about the request and the resolved pair."""

    snapshot: dict[str, Any]
    address: str
    symbol: str
    timeframe: str
    unit: str
    chain: str
    pair_address: str
    source: str
    width: int
    height: int

    def identity(self) -> tuple[dict[str, Any], list[str], list[str]]:
        """(query, symbols, addresses) shared by both payloads."""
        snapshot = self.snapshot
        query = {"address": self.address, "symbol": self.symbol, "chain": self.chain}
        symbols = [snapshot["symbol"]] if snapshot["symbol"] else []
        addresses = [snapshot["address"]] if snapshot["address"] else []
        return query, symbols, addresses


def candles_result(
    answer: ChartAnswer,
    analysis: dict[str, Any],
    closes: list[float],
    candles: list[dict[str, Any]],
) -> ToolResult:
    """Full payload: ASCII chart + the TA digest from the parsed candles."""
    query, symbols, addresses = answer.identity()
    chart_text = _render_chart(
        ChartCanvas(
            snapshot=answer.snapshot,
            timeframe=answer.timeframe,
            unit=answer.unit,
            candles=candles,
            analysis=analysis,
            closes=closes,
            source=answer.source,
            width=answer.width,
            height=answer.height,
        )
    )
    return ToolResult(
        tool="chart",
        ok=True,
        data={
            "query": query,
            "symbol": answer.snapshot["symbol"],
            "name": answer.snapshot["name"],
            "chain": answer.chain,
            "pair_address": answer.pair_address,
            "timeframe": answer.timeframe,
            "candle_unit": answer.unit,
            "candles_shown": len(candles),
            "indicators_available": True,
            "chart_text": chart_text,
            "sparkline": _sparkline(closes, answer.width),
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
        symbols=symbols,
        addresses=addresses,
        source=answer.source or "ohlcv:unknown",
    )


def _momentum_text(snapshot: dict[str, Any]) -> str:
    """Real snapshot momentum ("1h +2.10% · 24h -0.40%"), never synthesized."""
    momentum: list[str] = []
    for window in ("change_1h", "change_6h", "change_24h"):
        value = snapshot[window]
        if value is not None:
            momentum.append(f"{window.removeprefix('change_')} {value:+.2f}%")
    return " · ".join(momentum) if momentum else "no change data"


def snapshot_result(answer: ChartAnswer) -> ToolResult:
    """Honest degraded payload: no candle source reachable, snapshot only.

    DexScreener exposes no OHLCV, so only the real snapshot metrics are
    returned — the text says so instead of inventing a chart.
    """
    query, symbols, addresses = answer.identity()
    notice = f"{_NO_CANDLES} — live snapshot: {_momentum_text(answer.snapshot)}"
    return ToolResult(
        tool="chart",
        ok=True,
        data={
            "query": query,
            "symbol": answer.snapshot["symbol"],
            "name": answer.snapshot["name"],
            "chain": answer.chain,
            "pair_address": answer.pair_address,
            "timeframe": answer.timeframe,
            "candle_unit": answer.unit,
            "candles_shown": 0,
            "indicators_available": False,
            "chart_text": notice,
            "sparkline": "",
            "candle_directions": "",
            "indicators": None,
            "snapshot": answer.snapshot,
            "summary_text": notice,
            "updated_at": datetime.now(UTC).isoformat(),
        },
        symbols=symbols,
        addresses=addresses,
        source="dexscreener:snapshot (no candles)",
    )


__all__ = ["ChartAnswer", "candles_result", "snapshot_result"]
