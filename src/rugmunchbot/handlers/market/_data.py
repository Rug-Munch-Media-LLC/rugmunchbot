"""market — redis cache + market-intel fetchers (split from handlers/market.py, 2026-09-18)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json

logger = get_logger("rmi_bot")


_TRENDING_TTL = 90  # within the 60-120s requirement


_TRENDING_KEY_PREFIX = "trending:"  # spec key: trending:{chain}:{window}


def _redis_client() -> Any:
    """Async Redis client (lazy import — fail-open when unavailable)."""
    from rugmunchbot._vendor.redis_client import get_redis_async

    return get_redis_async()


async def _cache_get_trending(chain: str, window: str) -> list[dict[str, Any]] | None:
    """Load the cached fused trending token list (None on any miss)."""
    r = _redis_client()
    if r is None:
        return None
    try:
        raw = await r.get(f"{_TRENDING_KEY_PREFIX}{chain}:{window}")
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, list) else None
    except Exception:  # fail-open by contract
        logger.debug("trending:cache_get_failed chain=%s window=%s", chain, window, exc_info=True)
        return None


async def _cache_set_trending(chain: str, window: str, tokens: list[dict[str, Any]]) -> None:
    """Write the fused trending token list to Redis. Fail-open."""
    r = _redis_client()
    if r is None:
        return
    try:
        await r.set(
            f"{_TRENDING_KEY_PREFIX}{chain}:{window}",
            json.dumps(tokens, default=str),
            ex=_TRENDING_TTL,
        )
    except Exception:  # fail-open by contract
        logger.debug("trending:cache_set_failed chain=%s window=%s", chain, window, exc_info=True)


async def _fetch_market_intel(
    path: str, params: dict[str, Any] | None = None, timeout_secs: float = 10
) -> dict[str, Any] | None:
    """GET a backend market-intel endpoint with the shared-secret header.

    Returns the parsed JSON body or None on any failure (network, non-200,
    malformed body). Callers render a graceful unavailable state on None.
    """
    try:
        body = await http_get_json(
            f"{BACKEND_URL}{path}",
            params=params,
            headers=backend_headers(),
            timeout=timeout_secs,
        )
        if body is None:
            logger.debug("market intel %s status non-200", path)
        return body
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("market intel %s fetch failed: %s", path, exc)
        return None
