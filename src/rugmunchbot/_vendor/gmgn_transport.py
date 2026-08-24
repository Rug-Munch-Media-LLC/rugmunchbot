# VENDORED 2026-08-24 from rmi-backend/app/databus/gmgn_transport.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""GMGN transport layer — split from gmgn_provider.py (de-godding 2026-08-23).

Methods operate on ``self`` state initialized by ``GMGNProvider.__init__``.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _normalize_chain(chain: str) -> str:
    """Normalize chain names to GMGN slugs."""
    return (chain or "ethereum").strip().lower()

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")

GMGN_BASE = "https://gmgn.ai"
GMGN_OPENAPI_BASE = "https://openapi.gmgn.ai"

_BROWSER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://gmgn.ai/?chain=sol",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}


class TransportMixin:
    def _agent_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge caller params with the Agent API exist-auth query params."""
        merged = dict(params or {})
        merged["timestamp"] = int(time.time())
        merged["client_id"] = str(uuid.uuid4())
        return merged

    async def _agent_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """GET against the Agent API with exist-auth + 429-aware backoff."""
        if not self.api_key:
            logger.debug("GMGN Agent API: no API key configured")
            return None
        now = time.time()
        if now < self._banned_until:
            logger.debug("GMGN Agent API: banned until %s", self._banned_until)
            return None
        if now < self._next_allowed_at:
            return None  # caller falls through to next source; no spam

        try:
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}{path}",
                headers=self.headers,
                params=self._agent_params(params),
            )
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN Agent API GET %s failed: %s", path, e)
            return None
        return self._handle_agent_response(resp, path)

    async def _agent_post(
        self, path: str, params: dict[str, Any] | None, body: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if not self.api_key:
            logger.debug("GMGN Agent API: no API key configured")
            return None
        now = time.time()
        if now < self._banned_until or now < self._next_allowed_at:
            return None
        try:
            resp = await self._request(
                "POST",
                f"{GMGN_BASE}{path}",
                headers=self.headers,
                params=self._agent_params(params),
                body=body,
            )
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN Agent API POST %s failed: %s", path, e)
            return None
        return self._handle_agent_response(resp, path)

    def _handle_agent_response(self, resp: httpx.Response, path: str) -> dict[str, Any] | None:
        """Validate Agent API response; apply rate-limit bookkeeping."""
        if resp.status_code == 429:
            reset = self._parse_reset(resp)
            if reset:
                self._banned_until = max(self._banned_until, float(reset))
            self._next_allowed_at = time.time() + 30
            logger.debug("GMGN Agent API 429 on %s (reset=%s)", path, reset)
            return None
        if resp.status_code == 401 or resp.status_code == 403:
            logger.debug("GMGN Agent API auth error %s on %s", resp.status_code, path)
            self._available = False
            return None
        if resp.status_code != 200:
            logger.debug("GMGN Agent API HTTP %s on %s", resp.status_code, path)
            return None
        try:
            payload = resp.json()
        except ValueError:
            logger.debug("GMGN Agent API non-JSON on %s (Cloudflare HTML?)", path)
            return None
        if not isinstance(payload, dict):
            return None
        code = payload.get("code")
        if code not in (0, None, 200):
            logger.debug(
                "GMGN Agent API error code=%s on %s: %s", code, path, payload.get("error") or payload.get("message")
            )
            return None
        return payload

    @staticmethod
    def _parse_reset(resp: httpx.Response) -> float | None:
        """Read rate-limit reset from X-RateLimit-Reset header or body reset_at."""
        raw = resp.headers.get("X-RateLimit-Reset")
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        try:
            body = resp.json()
            raw = body.get("reset_at") if isinstance(body, dict) else None
            if raw:
                return float(raw)
        except ValueError:
            pass
        return None

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

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
                proxy_resp = await self._proxy_client.request(method, url, headers=headers, params=params, json=json)
            except (httpx.HTTPError, ConnectionError, TimeoutError):
                self._rotate_proxy()
                return await self.client.request(method, url, headers=headers, params=params, json=json)
            if proxy_resp.status_code in (403, 429) or proxy_resp.status_code >= 500:
                self._rotate_proxy()
                return await self.client.request(method, url, headers=headers, params=params, json=json)
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

def _f(value: Any) -> float:
    """Best-effort float coercion (strings from the API included)."""
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sum_top_pct(rows: list[dict[str, Any]], cap: int = 10) -> float | None:
    """Sum supply % of the first ``cap`` holders (Agent API rows)."""
    if not isinstance(rows, list):
        return None
    total = 0.0
    seen = 0
    for h in rows:
        pct = h.get("amount_percentage")
        try:
            if pct is None:
                continue
            total += float(pct)
            seen += 1
            if seen >= cap:
                break
        except (TypeError, ValueError):
            continue
    return total if seen else None


# ── Singleton ──────────────────────────────────────────────────────

_gmgn_provider: GMGNProvider | None = None

