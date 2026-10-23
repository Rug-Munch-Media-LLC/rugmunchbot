"""The only network seam of the Polymarket engine.

One injectable function (``fetch_json``) keeps every module testable without
patching: production uses :func:`get_json`, tests pass a fixture reader.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.polymarket.http")

FetchJson = Callable[[str, dict[str, Any]], Awaitable[Any]]

TIMEOUT = 12.0
USER_AGENT = "rugmunchbot-polymarket/1.0 (+https://rugmunch.io)"


class PolymarketApiError(RuntimeError):
    """A public Polymarket endpoint answered with an error or bad JSON."""


async def get_json(url: str, params: dict[str, Any]) -> Any:
    """GET a JSON document from a public Polymarket endpoint."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise PolymarketApiError(f"{url} unreachable: {exc}") from exc
    if response.status_code != 200:
        raise PolymarketApiError(f"{url} returned HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise PolymarketApiError(f"{url} returned invalid JSON") from exc
