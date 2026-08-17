from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

import httpx

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json

"""Own-data-first data resolver for RugMunchBot.

Enforces the "own data first" hierarchy for every data access in the bot:

    1. Scanner/Indexer data (ClickHouse lite-indexer, our scanners)  -> tier 1
    2. RAG knowledge base (Qdrant, 9 collections wired into chat)   -> tier 2
    3. DataBus providers (30+ APIs, cached by the backend)          -> tier 3
    4. External APIs (DexScreener, CoinGecko, alternative.me, ...)  -> fallback only
    5. Web search (SearXNG)                                         -> last resort

Every call goes through :func:`resolve_with_fallback`, which tries the
own-data primary first and only falls back to the external API on failure,
timeout, or invalid payload. Each call logs ``data_source`` so reliance on
external providers can be tracked (see :func:`get_source_stats`).

Design notes
------------
* Primary functions must be async and return the SAME shape as the fallback
  (the caller renders one contract regardless of source).
* A primary returning ``None``/empty is treated as a miss -> fallback runs.
* Counters are in-process only; wire them to metrics/DATA_SOURCE logs to
  build a reliance dashboard.
"""
logger = get_logger("rmi_bot.data_resolver")

T = TypeVar("T")

# ── Reliance tracking ────────────────────────────────────────────────────
# resolver_name -> {"own_data": int, "fallback": int, "both_failed": int}
_SOURCE_COUNTS: dict[str, dict[str, int]] = defaultdict(lambda: {"own_data": 0, "fallback": 0, "both_failed": 0})


def get_source_stats() -> dict[str, dict[str, int]]:
    """Return data-source usage counters for reliance tracking.

    Example:
        {"alert_price": {"own_data": 12, "fallback": 3, "both_failed": 0}}
    """
    return {k: dict(v) for k, v in sorted(_SOURCE_COUNTS.items())}


def _record(resolver_name: str, source: str) -> None:
    _SOURCE_COUNTS[resolver_name][source] += 1


# ── Core resolver ────────────────────────────────────────────────────────


async def resolve_with_fallback(
    resolver_name: str,
    primary_fn: Callable[[], Awaitable[Any]],
    fallback_fn: Callable[[], Awaitable[T]],
    *,
    timeout_secs: float = 5.0,
    is_valid: Callable[[Any], bool] | None = None,
) -> T:
    """Try own-data ``primary_fn`` first, fall back to ``fallback_fn``.

    Args:
        resolver_name: Stable identifier for this data access (e.g.
            ``"dex_price"``). Used in DATA_SOURCE logs and stats.
        primary_fn: Own-data callable (scanner cache / RAG / DataBus).
        fallback_fn: External API callable (DexScreener, CoinGecko, ...).
        timeout_secs: Max time to wait on the primary before falling back.
        is_valid: Optional validator. If it returns False the result is
            treated as a miss and the fallback runs.

    Returns:
        The result of the first source that succeeds. Raises the fallback
        error when both sources fail (callers must handle that).
    """
    start = time.perf_counter()

    # ── 1. Own data (primary) ──────────────────────────────────────────
    try:
        result = await asyncio.wait_for(primary_fn(), timeout=timeout_secs)
        if is_valid is None or is_valid(result):
            _record(resolver_name, "own_data")
            latency_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "DATA_SOURCE=own_data resolver=%s latency_ms=%.1f",
                resolver_name,
                latency_ms,
            )
            return result
        logger.debug("DATA_SOURCE=own_data_invalid resolver=%s", resolver_name)
    except TimeoutError:
        logger.debug("DATA_SOURCE=own_data_timeout resolver=%s timeout=%ss", resolver_name, timeout_secs)
    except Exception as exc:
        logger.debug("DATA_SOURCE=own_data_error resolver=%s err=%s", resolver_name, _short(exc))

    # ── 2. External API (fallback) ─────────────────────────────────────
    try:
        result = await fallback_fn()
        _record(resolver_name, "fallback")
        latency_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "DATA_SOURCE=fallback_external resolver=%s latency_ms=%.1f",
            resolver_name,
            latency_ms,
        )
        return result
    except Exception as exc:
        _record(resolver_name, "both_failed")
        logger.error(
            "DATA_SOURCE=both_failed resolver=%s err=%s",
            resolver_name,
            _short(exc),
        )
        raise


# ── Backend own-data helper ──────────────────────────────────────────────


async def backend_get_json(
    path: str,
    params: dict[str, Any] | None = None,
    timeout_secs: float = 5.0,
) -> dict[str, Any] | None:
    """GET a backend (own-data) endpoint with the shared-secret header.

    Returns the parsed JSON object, or ``None`` on network/HTTP/parse failure.
    """
    try:
        body = await http_get_json(
            f"{BACKEND_URL}{path}",
            params=params,
            headers=backend_headers(),
            timeout=timeout_secs,
        )
        if body is None:
            logger.debug("backend_get_json %s status non-200", path)
        return body
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("backend_get_json %s failed: %s", path, _short(exc))
        return None


# ── Reusable resolvers ───────────────────────────────────────────────────


async def resolve_token_price(
    address: str,
    chain: str,
    fallback_fn: Callable[[], Awaitable[T]],
    *,
    timeout_secs: float = 4.0,
) -> T:
    """Resolve token price: scanner cache first, external API fallback.

    Primary: backend scanner cache ``/api/v2/scanner/token/price/{chain}/{address}``
    (populated by our own scanners from ClickHouse/DataBus).
    """

    async def _primary() -> dict[str, Any] | None:
        body = await backend_get_json(
            f"/api/v2/scanner/token/price/{chain}/{address}",
            timeout_secs=timeout_secs,
        )
        if not body or not body.get("cached") or not body.get("data"):
            return None
        return body["data"]

    return await resolve_with_fallback(
        f"token_price:{chain}:{address[:10]}",
        _primary,
        fallback_fn,
        timeout_secs=timeout_secs,
        is_valid=lambda d: bool(d and d.get("price_usd")),
    )


async def resolve_market_global(
    fallback_fn: Callable[[], Awaitable[T]],
    *,
    timeout_secs: float = 4.0,
) -> T:
    """Resolve global market data (mcap, BTC dominance): backend first.

    Primary: backend market intel ``/api/v1/market/overview`` (cached, TTL 120s).
    """

    async def _primary() -> dict[str, Any] | None:
        body = await backend_get_json("/api/v1/market/overview", timeout_secs=timeout_secs)
        if not body or not body.get("total_market_cap"):
            return None
        return body

    return await resolve_with_fallback(
        "market_global",
        _primary,
        fallback_fn,
        timeout_secs=timeout_secs,
        is_valid=lambda d: bool(d and d.get("total_market_cap")),
    )


async def resolve_fear_greed(
    fallback_fn: Callable[[], Awaitable[T]],
    *,
    timeout_secs: float = 4.0,
) -> T:
    """Resolve Fear & Greed index: backend first, external API fallback.

    Primary: backend market intel ``/api/v1/market/fear-greed`` (cached).
    """

    async def _primary() -> dict[str, Any] | None:
        body = await backend_get_json("/api/v1/market/fear-greed", timeout_secs=timeout_secs)
        if not body or not body.get("value"):
            return None
        return body

    return await resolve_with_fallback(
        "fear_greed",
        _primary,
        fallback_fn,
        timeout_secs=timeout_secs,
        is_valid=lambda d: bool(d and d.get("value")),
    )


async def resolve_news(
    fallback_fn: Callable[[], Awaitable[T]],
    *,
    timeout_secs: float = 4.0,
) -> T:
    """Resolve news/trending items: own news network first, external fallback.

    Primary: backend news feed (RAG-scored, bias/sentiment rated). The
    backend also caches trending stories, so this is our own data pipeline.
    """

    async def _primary() -> list[dict[str, Any]] | None:
        body = await backend_get_json(
            "/api/v1/news/trending",
            params={"window_hours": 168, "limit": 5},
            timeout_secs=timeout_secs,
        )
        if not body:
            return None
        items = body.get("items") or body.get("articles") or []
        return items if items else None

    return await resolve_with_fallback(
        "news_trending",
        _primary,
        fallback_fn,
        timeout_secs=timeout_secs,
        is_valid=lambda d: bool(d),
    )


def _short(exc: Exception) -> str:
    msg = str(exc)
    return msg[:150] if len(msg) > 150 else msg
