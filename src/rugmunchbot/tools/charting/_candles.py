"""chart OHLCV candles — own data first, GeckoTerminal direct fallback.

Extracted verbatim from the former single-module ``tools/charting.py``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.data_resolver import resolve_with_fallback
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import BackendApiError, http_get_json
from rugmunchbot.tools.charting._constants import (
    _GECKO_NETWORKS,
    _GECKO_OHLCV_URL,
    _Candle,
    _CandleResult,
)
from rugmunchbot.tools.charting._pairs import _normalize_chain
from rugmunchbot.tools.registry import backend_json_get

logger = get_logger("rmi_bot.tools.charting")


async def _backend_candles(chain: str, pair_address: str, unit: str) -> _CandleResult:
    """Own-data candles via the backend RugCharts engine (ClickHouse first,
    GeckoTerminal server-side fallback). Returns (candles, source_label)."""
    body: dict[str, Any] | None
    try:
        body = await backend_json_get(
            f"/api/v1/rugcharts/ohlcv/{chain}/{pair_address}",
            params={"timeframe": unit, "limit": 100},
            timeout=6.0,
        )
    except BackendApiError as exc:
        # Own-data leg of the candle chain (GeckoTerminal fallback follows):
        # a dead backend degrades to a miss, not a tool-level failure.
        logger.debug("charting candles backend miss (%s): %s", exc.status_code, exc)
        return [], ""
    if not isinstance(body, dict):
        return [], ""
    data = body.get("data")
    candles = data.get("candles") if isinstance(data, dict) else None
    if isinstance(candles, list) and candles:
        return candles, f"backend:/api/v1/rugcharts/ohlcv/{chain}/{pair_address}"
    return [], ""


async def _gecko_candles(chain: str, pair_address: str, unit: str) -> _CandleResult:
    """GeckoTerminal direct OHLCV (same query pattern as the backend helper
    ``app/routers/rugcharts_helpers._fetch_ohlcv_gecko``)."""
    network = _GECKO_NETWORKS.get(_normalize_chain(chain), _normalize_chain(chain))
    body = await http_get_json(
        _GECKO_OHLCV_URL.format(network=network, pair=pair_address, unit=unit),
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


async def _fetch_candles(
    chain: str, pair_address: str, unit: str, gecko_unit: str
) -> _CandleResult:
    """Run the own-data-first candle cascade (backend -> GeckoTerminal).

    ``unit`` is the backend candle timeframe ("1h"/"1d"); ``gecko_unit`` the
    GeckoTerminal endpoint suffix ("hour"/"day").
    """
    try:
        return await resolve_with_fallback(
            f"chart_ohlcv:{unit}:{chain}:{pair_address[:10]}",
            lambda: _backend_candles(chain, pair_address, unit),
            lambda: _gecko_candles(chain, pair_address, gecko_unit),
            timeout_secs=5.0,
            is_valid=lambda res: bool(res and isinstance(res[0], list) and len(res[0]) >= 2),
        )
    except Exception as exc:  # pragma: no cover — both sources failed
        logger.debug("chart_ohlcv both sources failed: %s", exc)
        return [], ""
