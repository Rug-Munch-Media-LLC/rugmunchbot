"""Read-only Polymarket clients: Gamma (markets) and Data API v2 (flow, wallet).

Endpoint + field names verified against the live public APIs on 2026-10-24:

* ``gamma-api.polymarket.com/markets`` -> list, fields ``question``,
  ``conditionId``, ``slug``, ``outcomePrices`` ("[\\"0.45\\",\\"0.55\\"]"),
  ``volumeNum``, ``volume24hr``, ``liquidityNum``, ``oneDayPriceChange``,
  ``endDateIso``, ``events[0].slug``, ``events[0].tags[].label``.
* ``data-api.polymarket.com/trades`` -> list, fields ``proxyWallet``, ``side``,
  ``outcome``, ``size`` (shares), ``price``, ``timestamp``,
  ``transactionHash``; honours ``market``, ``filterType=CASH``,
  ``filterAmount``, ``takerOnly``, ``limit``.
* ``data-api.polymarket.com/traded?user=`` -> ``{"user": ..., "traded": N}``
  (distinct markets traded all-time).
* ``data-api.polymarket.com/value?user=`` -> ``[{"user": ..., "value": 0}]``.
* ``data-api.polymarket.com/positions?user=`` -> list (``[]`` when flat).

No write/sign capability exists in this module or anywhere in the package.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.polymarket.http import FetchJson, PolymarketApiError, get_json
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.polymarket.client")

GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
DATA_TRADES = "https://data-api.polymarket.com/trades"
DATA_TRADED = "https://data-api.polymarket.com/traded"
DATA_VALUE = "https://data-api.polymarket.com/value"
DATA_POSITIONS = "https://data-api.polymarket.com/positions"


class PolymarketData:
    """Thin, read-only facade over the two public Polymarket APIs."""

    def __init__(self, fetch_json: FetchJson = get_json) -> None:
        self._fetch = fetch_json

    async def markets(self, limit: int = 60) -> list[dict[str, Any]]:
        payload = await self._fetch(
            GAMMA_MARKETS,
            {
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
                "limit": limit,
            },
        )
        return (
            [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        )

    async def trades(
        self, condition_id: str, *, min_cash: float, limit: int = 20
    ) -> list[dict[str, Any]]:
        payload = await self._fetch(
            DATA_TRADES,
            {
                "market": condition_id,
                "limit": limit,
                "filterType": "CASH",
                "filterAmount": int(min_cash),
                "takerOnly": "true",
            },
        )
        return (
            [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        )

    async def traded_count(self, wallet: str) -> int | None:
        """Distinct markets the wallet has traded, or ``None`` when unavailable."""
        try:
            payload = await self._fetch(DATA_TRADED, {"user": wallet})
        except PolymarketApiError as exc:
            logger.debug("traded_count miss for %s: %s", wallet, exc)
            return None
        if isinstance(payload, dict) and isinstance(payload.get("traded"), int):
            return int(payload["traded"])
        return None

    async def wallet_value(self, wallet: str) -> float | None:
        payload = await self._fetch(DATA_VALUE, {"user": wallet})
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if isinstance(row, dict) and row.get("value") is not None:
                try:
                    return float(row["value"])
                except (TypeError, ValueError):
                    return None
        return None

    async def wallet_positions(self, wallet: str, limit: int = 50) -> list[dict[str, Any]]:
        payload = await self._fetch(DATA_POSITIONS, {"user": wallet, "limit": limit})
        return (
            [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        )
