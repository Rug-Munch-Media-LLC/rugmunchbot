"""Shared async HTTP client factory for the RugMunchBot tool registry.

All tools use httpx.AsyncClient — never the blocking ``requests`` / urllib
synchronous path — so the bot event loop is never blocked by a data call.

Connection pooling: one message can chain 20-30 tool calls (token identity +
scanner + enrichment), and the old per-call ``httpx.AsyncClient`` creation
spawned a fresh TLS handshake every time. The helpers now use a small set of
module-level keep-alive clients (:func:`get_shared_client`), one per timeout
bucket, so TCP/TLS connections are reused across calls. The clients are lazy
(created on first use — tests faking ``httpx.AsyncClient`` still intercept),
re-created if closed, and released by :func:`close_client` (wire into the bot
shutdown hook). :func:`make_client` is kept for callers that genuinely need
an isolated one-shot client (e.g. ``async with make_client(...)``).

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

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.budget import budget, maybe_log_summary, provider_for_url

logger = get_logger("rmi_bot.tools.client")

DEFAULT_TIMEOUT = 8.0

# httpx connection-pool limits for the shared clients. Keep-alive reuse is the
# point: without it every tool call pays a fresh TLS handshake (~20-30 per
# message). 100 pooled connections / 20 keep-alive matches httpx defaults but
# is stated explicitly so the pooling contract is visible.
_MAX_CONNECTIONS = 100
_MAX_KEEPALIVE = 20

_shared_clients: dict[float, httpx.AsyncClient] = {}


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """Create an isolated AsyncClient with connect/read/write timeouts.

    Kept for callers that manage their own lifecycle (``async with``); the
    http_* helpers below use the pooled :func:`get_shared_client` instead.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=3.0, read=timeout, write=8.0),
        follow_redirects=True,
        headers={"User-Agent": "RugMunchBot/1.0"},
    )


def get_shared_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """Return the pooled keep-alive AsyncClient for a timeout bucket.

    Lazy singleton per distinct ``timeout`` value (callers pass 6.0 / 8.0 /
    10.0 / 30.0 — a tiny bounded set), so per-call timeout semantics are
    preserved exactly while connections stay alive across calls. The client
    is created on first use, re-created if it has been closed, and never
    torn down per request. Tests faking ``httpx.AsyncClient`` on this module
    intercept here because the constructor resolves at call time; the client
    is also re-created when the module-level class changes (each test file
    installs its own fake class, and a stale instance would read the wrong
    class state).
    """
    client = _shared_clients.get(timeout)
    client_cls = httpx.AsyncClient
    if client is None or type(client) is not client_cls or getattr(client, "is_closed", False):
        client = client_cls(
            timeout=httpx.Timeout(timeout, connect=3.0, read=timeout, write=8.0),
            follow_redirects=True,
            headers={"User-Agent": "RugMunchBot/1.0"},
            limits=httpx.Limits(max_connections=_MAX_CONNECTIONS, max_keepalive_connections=_MAX_KEEPALIVE),
        )
        _shared_clients[timeout] = client
    return client


async def close_client() -> None:
    """Close every pooled client and drop the references.

    Call from the bot shutdown hook (fail-open: never raises). The next
    request after shutdown lazily re-creates a client.
    """
    for client in _shared_clients.values():
        aclose = getattr(client, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:  # fail-open by contract
                logger.debug("shared client close failed", exc_info=True)
    _shared_clients.clear()


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
        client = get_shared_client(timeout)
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
        client = get_shared_client(timeout)
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
        client = get_shared_client(timeout)
        r = await client.post(url, json=json or {}, headers=headers)
        if r.status_code != 200:
            return None
        body = r.json()
        return body if isinstance(body, dict) else None
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError):
        return None
    finally:
        maybe_log_summary()
