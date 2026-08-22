# Split from the vendored gmgn_provider.py during the de-godding program
# 2026-08-22 (transport/infra seam). Method bodies moved VERBATIM — see the
# facade ``gmgn_provider.py`` for provenance and the singleton.
"""GMGN transport layer: constants, chain slugs, proxy-first HTTP mixin."""

from __future__ import annotations

import os
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.proxy_rotator import get_proxy_rotator

logger = get_logger(__name__)

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_BASE = "https://gmgn.ai"
# Official GMGN Agent API (used by the gmgn-cli SDK / openapi.gmgn.ai).
# Auth: X-APIKEY header + timestamp (unix seconds) + client_id (UUID) query
# params — see OpenApiClient.ts / signer.ts in GMGNAI/gmgn-skills.
GMGN_OPENAPI_BASE = "https://openapi.gmgn.ai"

# Browser-like headers — GMGN's public API rejects plain curl/httpx UAs.
_BROWSER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://gmgn.ai/?chain=sol",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

# Cache TTLs (seconds)
TRADE_CACHE_TTL = 30
TOKEN_CACHE_TTL = 120
HOLDER_CACHE_TTL = 300


def _normalize_chain(chain: str) -> str:
    """Map chain names to GMGN's short chain slugs (sol, eth, bsc, base...)."""
    aliases = {
        "solana": "sol",
        "sol": "sol",
        "ethereum": "eth",
        "eth": "eth",
        "mainnet": "eth",
        "bsc": "bsc",
        "bnb": "bsc",
        "binance": "bsc",
        "base": "base",
        "polygon": "polygon",
        "matic": "polygon",
        "arbitrum": "arbitrum",
        "arb": "arbitrum",
        "optimism": "optimism",
        "op": "optimism",
        "avalanche": "avax",
        "avax": "avax",
        "fantom": "fantom",
        "ftm": "fantom",
        "tron": "tron",
        "trx": "tron",
        "robinhood": "robinhood",
    }
    return aliases.get(chain.lower(), chain.lower())


class _GMGNTransportMixin:
    """Proxy-first HTTP transport + lifecycle for :class:`GMGNProvider`.

    Attributes set here (``client``, ``_proxy_client``, ``headers``,
    ``api_key``, ``_available``) are consumed verbatim by the endpoint
    mixins; keep names stable — tests and gmgn_fallbacks bind them.
    """

    def __init__(self) -> None:
        self.api_key = GMGN_API_KEY
        self.headers = dict(_BROWSER_HEADERS)
        if self.api_key:
            # GMGN Agent API auth convention (official SDK: X-APIKEY header).
            self.headers["X-APIKEY"] = self.api_key
        # Direct client is the always-available fallback egress.
        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        # Proxy-first egress when residential proxies are configured.
        self._rotator = get_proxy_rotator()
        self._proxy_url: str | None = None
        self._proxy_client: httpx.AsyncClient | None = None
        if self._rotator.enabled:
            self._proxy_url = self._rotator.get_rotating_proxy()
            self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)
        # Public API works without a key; _available is only used to decide
        # whether to try GMGN at all (True = always try, fallbacks cover failures).
        self._available = True

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Proxy-first HTTP request with a single direct retry.

        When a proxied client is configured it is tried first; on a proxy
        failure (403/5xx/timeout — Cloudflare IP-block family) the proxy is
        marked failed (cooldown) and the request is retried once against the
        direct client. Transport errors propagate to callers, which already
        guard each fetch with try/except.
        """
        if self._proxy_client is not None:
            try:
                proxy_resp = await self._proxy_client.request(
                    method, url, headers=headers, params=params, json=json
                )
            except (httpx.HTTPError, ConnectionError, TimeoutError):
                self._rotate_proxy()
                return await self.client.request(
                    method, url, headers=headers, params=params, json=json
                )
            if proxy_resp.status_code in (403, 429) or proxy_resp.status_code >= 500:
                self._rotate_proxy()
                return await self.client.request(
                    method, url, headers=headers, params=params, json=json
                )
            return proxy_resp
        return await self.client.request(method, url, headers=headers, params=params, json=json)

    def _rotate_proxy(self) -> None:
        """Mark the current proxy failed and pick the next healthy egress."""
        self._rotator.mark_failed(self._proxy_url)
        self._proxy_url = self._rotator.next_after_failure()
        self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)

    async def close(self) -> None:
        """Clean up resources."""
        await self.client.aclose()
        if self._proxy_client is not None:
            await self._proxy_client.aclose()
