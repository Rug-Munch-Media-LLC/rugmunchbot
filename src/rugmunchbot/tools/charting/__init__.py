"""chart tool — compact ASCII price-action chart + TA digest for RugMunchBot.

Modularized from the former single-module ``tools/charting.py`` (2026-08-22).
This ``__init__`` is a pure re-export shim: every public name and signature is
unchanged, and callers keep importing ``tools.charting`` exactly as before.

Answers *"chart for $BONK"* / *"price chart"* with a text-only, Telegram-
monospace friendly chart (wrapped in backticks by the chat layer). No image
generation.

Data cascade (own data first — same order as ``tools/ta.py``):

  1. OUR backend ``GET /api/v1/rugcharts/ohlcv/{chain}/{pair}`` — the RugCharts
     engine (chain-indexer ClickHouse materialized view first, GeckoTerminal
     server-side fallback). ``timeframe=1h`` for 24h charts, ``1d`` for 7d.
  2. GeckoTerminal direct (``/ohlcv/hour`` or ``/ohlcv/day``) — used when the
     backend is unreachable or returns no candles.
  3. DexScreener pair snapshot — REAL snapshot metrics when no candle source
     is reachable (honest degraded chart; never fabricated).

The chart body: a one-line ASCII sparkline (close series), a per-candle
direction row (candlestick form), a compact vertical price ladder, and the TA
digest (RSI-14 / EMA-9/21 / MACD / support-resistance / trend — reused pure
math from ``ta_indicators.analyze_candles``).

Layout:
  * ``_constants.py`` — networks, chain aliases, timeframes, spark chars
  * ``_pairs.py``     — DexScreener pair resolution + real snapshot metrics
  * ``_candles.py``   — own-data-first OHLCV cascade (backend -> Gecko)
  * ``_render.py``    — ASCII sparkline / direction row / ladder / digest
  * ``_tool.py``      — chart handler + register_tool

Self-contained on purpose: chain maps / pair resolution / candle cascade are
local copies because the codebase convention keeps these per-tool to avoid
private cross-tool imports (keep in sync with ``tools/ta.py``).
"""

from __future__ import annotations

from rugmunchbot.tools.charting._candles import (
    _backend_candles as _backend_candles,
    _fetch_candles as _fetch_candles,
    _gecko_candles as _gecko_candles,
)
from rugmunchbot.tools.charting._constants import (
    _CHAIN_ALIASES as _CHAIN_ALIASES,
    _GECKO_NETWORKS as _GECKO_NETWORKS,
    _GECKO_OHLCV_URL as _GECKO_OHLCV_URL,
    _SPARK_CHARS as _SPARK_CHARS,
    _TIMEFRAMES as _TIMEFRAMES,
    _Candle as _Candle,
    _CandleResult as _CandleResult,
)
from rugmunchbot.tools.charting._pairs import (
    _is_pair as _is_pair,
    _normalize_chain as _normalize_chain,
    _normalize_pairs as _normalize_pairs,
    _pair_snapshot as _pair_snapshot,
    _pick_best_pair as _pick_best_pair,
    _resolve_pair as _resolve_pair,
    _to_float as _to_float,
)
from rugmunchbot.tools.charting._render import (
    _bias as _bias,
    _candle_direction_row as _candle_direction_row,
    _levels_line as _levels_line,
    _price_ladder as _price_ladder,
    _render_chart as _render_chart,
    _slice_candles as _slice_candles,
    _sparkline as _sparkline,
    _summary_text as _summary_text,
)
from rugmunchbot.tools.charting._tool import (
    chart as chart,
    register_tool as register_tool,
)

__all__ = [
    "chart",
    "register_tool",
]
