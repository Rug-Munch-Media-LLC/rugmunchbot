#!/usr/bin/env python3
"""
RugMunchBot -- RugCharts data fetchers (degodded 2026-08-14).

Extracted from ``handlers/scanning.py``: OHLCV candles + rug-score intel
fetched from the backend RugCharts engine (the same routes the frontend
chart uses). Used by the /ta handler; re-exported by ``handlers.scanning``
so existing importers keep working unchanged.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


async def _fetch_rugcharts_ohlcv(client: httpx.AsyncClient, chain: str, pair_address: str) -> list[dict[str, Any]]:
    """Fetch OHLCV candles from the backend RugCharts engine (own data).

    Same route the frontend chart uses:
    ``GET /api/v1/rugcharts/ohlcv/{chain}/{pair}?timeframe=1h&limit=100``.
    Returns chronological candles (oldest → newest) or ``[]``.
    """
    if not pair_address or not chain:
        return []
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/rugcharts/ohlcv/{chain}/{pair_address}",
            params={"timeframe": "1h", "limit": 100},
            headers=backend_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            body = r.json()
            data = body.get("data") or body
            candles = data.get("candles") or []
            if isinstance(candles, list):
                return candles
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("rugcharts ohlcv fetch failed: %s", e)
    return []


async def _fetch_rugcharts_intel(client: httpx.AsyncClient, chain: str, pair_address: str) -> dict[str, Any]:
    """Fetch RugCharts intel (rug score + volume authenticity) for risk overlay."""
    if not pair_address or not chain:
        return {}
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/rugcharts/intel/{chain}/{pair_address}",
            headers=backend_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            body = r.json()
            data = body.get("data") or body
            return data if isinstance(data, dict) else {}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("rugcharts intel fetch failed: %s", e)
    return {}
