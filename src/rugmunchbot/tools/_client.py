"""Shared async HTTP client factory for the RugMunchBot tool registry.

All tools use httpx.AsyncClient — never the blocking ``requests`` / urllib
synchronous path — so the bot event loop is never blocked by a data call.
Each client is created per-call with a bounded timeout (no shared mutable
client, no leaked connections).

Credit efficiency: every request is gated through ``tools/budget.py`` BEFORE
the client is created. The provider bucket (dexscreener, coingecko, birdeye,
backend, ...) is derived from the URL (or an explicit ``provider=`` kwarg);
when the per-minute cap is exhausted the call is NOT made — it returns
``None`` (a miss), so the tool's own "one source first, fallback only on
miss" chain moves to the next provider instead of burning free credits on a
rate-limited source. Cumulative per-provider call counts are logged via
:func:`maybe_log_summary`.
"""

from __future__ import annotations

from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.budget import budget, maybe_log_summary, provider_for_url

logger = get_logger("rmi_bot.tools.client")

DEFAULT_TIMEOUT = 8.0


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """Create an isolated AsyncClient with connect/read/write timeouts."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=3.0, read=timeout, write=8.0),
        follow_redirects=True,
        headers={"User-Agent": "RugMunchBot/1.0"},
    )


async def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    provider: str | None = None,
) -> dict[str, Any] | None:
    """GET a URL and parse JSON. Returns None on any failure (never raises).

    ``provider`` optionally overrides the auto-detected budget bucket (useful
    when the host alone is ambiguous). When the provider is over its per-minute
    budget the call is skipped and ``None`` is returned immediately — the
    tool's fallback chain treats that as a miss.
    """
    bucket = provider_for_url(url, provider)
    if not budget.acquire(bucket):
        logger.debug("tool_api_budget skip provider=%s url=%.100s", bucket, url)
        return None
    try:
        async with make_client(timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError):
        return None
    finally:
        maybe_log_summary()


async def http_get_any(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    provider: str | None = None,
) -> Any:
    """GET a URL and parse JSON as a dict OR a list (never assumes a dict body).

    ``http_get_json`` only returns dict bodies — sources like DefiLlama
    ``/v2/chains`` and CoinGecko ``/coins/markets`` return arrays, so tools
    that consume them need this list-capable variant. Same budget gate as
    :func:`http_get_json`: the provider bucket is derived from the URL (or
    ``provider``) and the call is skipped (returns ``None``) when the
    per-minute cap is exhausted — the tool's fallback chain treats that as a
    miss. Returns ``None`` on any failure (never raises).
    """
    bucket = provider_for_url(url, provider)
    if not budget.acquire(bucket):
        logger.debug("tool_api_budget skip provider=%s url=%.100s", bucket, url)
        return None
    try:
        async with make_client(timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return None
            return r.json()
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError):
        return None
    finally:
        maybe_log_summary()


async def http_post_json(
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    provider: str | None = None,
) -> dict[str, Any] | None:
    """POST JSON and parse the response. Returns None on any failure.

    Budget-gated like :func:`http_get_json`.
    """
    bucket = provider_for_url(url, provider)
    if not budget.acquire(bucket):
        logger.debug("tool_api_budget skip provider=%s url=%.100s", bucket, url)
        return None
    try:
        async with make_client(timeout) as client:
            r = await client.post(url, json=json or {}, headers=headers)
            if r.status_code != 200:
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError):
        return None
    finally:
        maybe_log_summary()
