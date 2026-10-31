"""chart tool handler — ASCII price-action chart + TA digest for a token.

Extracted verbatim from the former single-module ``tools/charting.py``.
"""

from __future__ import annotations

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
    _slice_candles,
)
from rugmunchbot.tools.charting._results import (
    ChartAnswer,
    candles_result,
    snapshot_result,
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
    """Compact ASCII price-action chart + TA digest for a token address or ``$SYMBOL``.

    Resolves the pair (DexScreener), fetches OHLCV own-data first (backend
    RugCharts/ClickHouse -> GeckoTerminal), slices to the requested window and
    renders a text-only chart (sparkline, per-candle direction row, price
    ladder) plus the TA digest from the pure ``ta_indicators`` math.

    Args:
        address: Token contract address (EVM or Solana).
        symbol: ``$SYMBOL`` (used when no address is given).
        chain: Optional chain filter ("ethereum", "solana", "base", ...).
        timeframe: "24h" (hourly candles) or "7d" (daily candles).
        width: Max sparkline / ladder character width.
        height: Ladder rows.

    Returns:
        ToolResult with ``chart_text``, ``sparkline``, ``candle_directions``,
        the indicator digest and a ``summary_text``. ``ok=False`` only when no
        pair resolves; with no reachable candles it still returns ``ok=True``
        and the REAL DexScreener snapshot — the chart is never fabricated.
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
    answer = ChartAnswer(
        snapshot=snapshot,
        address=address,
        symbol=symbol,
        timeframe=tf,
        unit=unit,
        chain=resolved_chain,
        pair_address=pair_address,
        source=source,
        width=width,
        height=height,
    )
    if not candles:
        # Honest degraded path: no candle source reachable, snapshot only.
        return snapshot_result(answer)
    analysis = analyze_candles(candles)
    if analysis is None:
        return ToolResult(
            tool="chart",
            ok=False,
            error="OHLCV candles returned but could not be parsed (malformed payload)",
        )
    closes = [float(c["close"]) for c in candles]
    return candles_result(answer, analysis, closes, candles)


# ── Registration (self-owned; invoked by tools/__init__._register_defaults) ─


def register_tool(reg: Any) -> None:
    """Register this tool on the shared registry (see trending_engine for notes)."""
    reg.register("chart", chart, timeout=12.0, ttl=120.0)
