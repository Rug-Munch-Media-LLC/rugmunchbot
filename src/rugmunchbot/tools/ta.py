"""ta.py - import surface preserved; implementation split into submodules."""
from __future__ import annotations
from .ta_p1 import logger, _GECKO_NETWORKS, _CHAIN_ALIASES, _GECKO_OHLCV_URL, _Candle, _CandleResult, _to_float, _normalize_chain, _normalize_pairs, _is_pair, _pick_best_pair, _resolve_pair, _pair_snapshot, _market_context  # noqa: F401
from .ta_p2 import _backend_candles, _gecko_candles, _fetch_candles, _verdict  # noqa: F401
from .ta_p3 import _prediction_read, _summary_text  # noqa: F401
from .ta_p4 import technical_analysis, register_tool  # noqa: F401
