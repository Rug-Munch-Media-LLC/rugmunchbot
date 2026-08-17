# VENDORED 2026-08-17 from rmi-backend/app/services/pryscraper_client.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Pryscraper Client — async HTTP client for the Pry web intelligence API.

Provides a circuit-breaker-protected, retry-capable client for all
Pry endpoints. Runs on the same Docker network (rmi_network) so it
can reach the `pry` container by hostname.

Usage:
    client = PryscraperClient()
    health = await client.check_health()
    result = await client.scrape("https://example.com")
    extracted = await client.extract("https://example.com")
    await client.aclose()
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

from rugmunchbot._vendor.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Circuit breaker ──────────────────────────────────────────────────────────
_pry_cb = AsyncCircuitBreaker(
    name="pryscraper",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── Models ────────────────────────────────────────────────────────────────────


class ScrapeRequest(BaseModel):
    """Request model for the Pry /v1/scrape endpoint."""

    url: str
    formats: list[str] | None = None
    onlyMainContent: bool = True
    timeout: int = 30
    bypassCloudflare: bool = True
    jsRender: bool = False


class ScrapeResult(BaseModel):
    """Normalised scrape result returned by the client."""

    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    source: str = "pryscraper"


class ExtractResult(BaseModel):
    """Normalised extraction result returned by the client."""

    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    source: str = "pryscraper"


# ── Client ────────────────────────────────────────────────────────────────────


class PryscraperClient:
    """Async HTTP client for the Pry web intelligence API.

    Connects to the ``pry`` container on ``rmi_network``.  Authenticates
    with the ``PRY_API_KEY`` value sent as ``Authorization: Bearer``.

    Circuit-breaker protects every outbound call.  Server errors (5xx,
    timeouts, connection errors) trip the breaker; client errors (4xx)
    do not.

    x402 paid endpoints (``/v1/scrape``, ``/v1/extract``, …) require
    payment verification.  If the ``PRY_X402_OFFLINE`` env var is
    ``"true"`` the client auto-verifies payments in offline mode so
    internal services can scrape without on-chain transactions.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        x402_offline: bool | None = None,
        timeout_scrape: float = 60.0,
        timeout_extract: float = 30.0,
        timeout_default: float = 15.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("PRYSCRAPER_URL", "http://pry:8002") or "").rstrip("/")
        self.api_key = api_key or os.getenv("PRY_API_KEY", "")
        self._x402_offline = (
            x402_offline
            if x402_offline is not None
            else os.getenv("PRY_X402_OFFLINE", "false").lower() in ("1", "true", "yes")
        )
        self._x402_payments: dict[str, float] = {}  # payment_id -> expiry

        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=httpx.Timeout(
                connect=5.0,
                read=timeout_default,
                write=10.0,
                pool=10.0,
            ),
            follow_redirects=True,
        )
        self._timeout_scrape = timeout_scrape
        self._timeout_extract = timeout_extract

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        timeout: float | None = None,
        x402_operation: str | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with circuit-breaker protection.

        Handles x402 payment-required responses by auto-paying in
        offline mode or raising a descriptive error.
        """
        url = f"{self.base_url}{path}"
        effective_timeout = timeout or self._client.timeout.read

        # Circuit-breaker check
        if not await _pry_cb.can_call():
            stale = _pry_cb.get_stale_result()
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(
                _pry_cb.name,
                _pry_cb.state,
                f"Pryscraper circuit OPEN — {path} not called",
            )

        # Retry loop: 3 attempts with exponential backoff
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                resp = await self._client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_data,
                    timeout=effective_timeout,
                )

                # Success (2xx) — record success on first attempt
                if resp.is_success:
                    data: dict[str, Any] = resp.json()
                    _pry_cb.set_stale_result(data)
                    await _pry_cb.record_success()
                    return data

                # x402 Payment Required — handle offline or raise
                if resp.status_code == 402:
                    handled = await self._handle_x402(resp, x402_operation)
                    if handled:
                        continue  # retry with payment header
                    # Cannot pay — return the 402 body as-is
                    return self._error_response(
                        "payment_required",
                        f"x402 payment required for {path}. Set PRY_X402_OFFLINE=true for offline mode.",
                        x402_body=resp.json() if resp.content else None,
                    )

                # Rate-limited (429) — wait and retry
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", 2**attempt))
                    await asyncio.sleep(retry_after)
                    continue

                # Server error (5xx) — trip circuit breaker
                if resp.status_code >= 500:
                    await _pry_cb.record_failure(is_server_error=True)
                    last_exc = OSError(f"Pry HTTP {resp.status_code} on {path}: {resp.text[:200]}")
                    if attempt < 3:
                        await asyncio.sleep(2**attempt)
                    continue

                # Client error (4xx) — don't trip circuit breaker
                await _pry_cb.record_failure(is_server_error=False)
                body = resp.json() if resp.content else {}
                error_msg = body.get("error", {}).get("message", resp.text[:200])
                return self._error_response(
                    "client_error",
                    f"Pry HTTP {resp.status_code} on {path}: {error_msg}",
                )

            except httpx.TimeoutException as exc:
                last_exc = exc
                await _pry_cb.record_failure(is_server_error=True)
                if attempt < 3:
                    await asyncio.sleep(2**attempt)

            except httpx.RequestError as exc:
                last_exc = exc
                await _pry_cb.record_failure(is_server_error=True)
                if attempt < 3:
                    await asyncio.sleep(2**attempt)

        # All retries exhausted
        await _pry_cb.record_failure(is_server_error=True)
        return self._error_response(
            "upstream_error",
            f"Pryscraper unreachable after 3 retries: {last_exc}"
            if last_exc
            else "Pryscraper unreachable after 3 retries",
        )

    async def _handle_x402(self, resp: httpx.Response, operation: str | None) -> bool:
        """Try to satisfy an x402 payment challenge.

        Returns ``True`` if a pre-verified payment was created so the
        caller should retry the original request with the payment header.
        Returns ``False`` if payment cannot be satisfied.
        """
        if not operation or not self._x402_offline:
            return False

        # In offline mode, create a fake verified payment
        import uuid

        payment_id = f"int_{uuid.uuid4().hex[:12]}"
        self._x402_payments[payment_id] = time.time() + 3600  # 1-hour TTL

        # Store the payment via the x402 endpoint so handler.is_paid() works
        try:
            pay_resp = await self._client.post(
                f"{self.base_url}/v1/x402/pay",
                headers=self._headers(),
                json={
                    "operation": operation,
                    "tx_hash": f"offline_{uuid.uuid4().hex[:16]}",
                    "payer_wallet": "internal",
                    "network": "base",
                    "asset": "USDC",
                    "amount_usd": 0.001,
                },
                timeout=10.0,
            )
            if pay_resp.is_success:
                data = pay_resp.json()
                pid = data.get("data", {}).get("payment_id", "")
                if pid:
                    self._x402_payments[pid] = time.time() + 3600
                    return True
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            logger.warning("x402 offline payment attempt failed", exc_info=True)

        return False

    def _error_response(
        self,
        code: str,
        message: str,
        x402_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a standardised error dict."""
        result: dict[str, Any] = {
            "success": False,
            "error": {"code": code, "message": message},
        }
        if x402_body:
            result["x402"] = x402_body
        return result

    # ── Public API ───────────────────────────────────────────────────────────

    async def check_health(self) -> dict[str, Any]:
        """Call the Pry ``/health`` endpoint.

        This endpoint is **free** — no auth or x402 payment required.
        """
        return await self._request("GET", "/health")

    async def scrape(
        self,
        url: str,
        *,
        formats: list[str] | None = None,
        only_main_content: bool = True,
        timeout: int = 30,
        bypass_cloudflare: bool = True,
        js_render: bool = False,
    ) -> ScrapeResult:
        """Scrape a single URL via Pry ``POST /v1/scrape``.

        Args:
            url: The URL to scrape.
            formats: Desired output formats (e.g. ``["markdown"]``).
            only_main_content: Extract only the main content.
            timeout: Scrape timeout in seconds (max 120).
            bypass_cloudflare: Auto-bypass Cloudflare protection.
            js_render: Render JavaScript before scraping.

        Returns:
            A ``ScrapeResult`` with ``success``, ``data``, ``error``.

        Note:
            This endpoint is gated behind x402 payment.  Set
            ``PRY_X402_OFFLINE=true`` to auto-verify payments for
            internal usage.
        """
        body = await self._request(
            "POST",
            "/v1/scrape",
            json_data={
                "url": url,
                "formats": formats,
                "onlyMainContent": only_main_content,
                "timeout": min(timeout, 120),
                "bypassCloudflare": bypass_cloudflare,
                "jsRender": js_render,
            },
            timeout=float(self._timeout_scrape),
            x402_operation="scrape",
        )

        if body.get("success"):
            return ScrapeResult(
                success=True,
                data=body.get("data", body),
                source="pryscraper",
            )
        return ScrapeResult(
            success=False,
            error=body.get("error", {}).get("message", str(body)),
            source="pryscraper",
        )

    async def extract(
        self,
        url: str,
        *,
        json_schema: dict[str, str] | None = None,
        timeout: int = 30,
    ) -> ExtractResult:
        """Extract structured data from a URL via Pry ``POST /v1/extract``.

        Args:
            url: The URL to extract from.
            json_schema: Optional schema for structured extraction.
            timeout: Extraction timeout in seconds.

        Returns:
            An ``ExtractResult`` with ``success``, ``data``, ``error``.
        """
        body = await self._request(
            "POST",
            "/v1/extract",
            json_data={
                "url": url,
                "json_schema": json_schema,
                "timeout": min(timeout, 120),
            },
            timeout=float(self._timeout_extract),
            x402_operation="extract",
        )

        if body.get("success"):
            return ExtractResult(
                success=True,
                data=body.get("data", body),
                source="pryscraper",
            )
        return ExtractResult(
            success=False,
            error=body.get("error", {}).get("message", str(body)),
            source="pryscraper",
        )

    async def search_news(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Search for news articles via Pry's intelligence engine.

        Uses the Pry ``/v1/intelligence`` endpoint (if available) or
        falls back to scraping a news search URL.

        Args:
            query: The news search query.
            limit: Maximum number of results.

        Returns:
            A dict with ``success``, ``articles``, ``error``.
        """
        # Try the intelligence endpoint first
        try:
            body = await self._request(
                "POST",
                "/v1/intelligence/search",
                json_data={"query": query, "limit": limit, "source": "news"},
                timeout=30.0,
            )
            if body.get("success"):
                return body
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

        # Fallback: use scrape + parse pattern for news search
        search_url = f"https://news.google.com/search?q={query}&hl=en-US"
        result = await self.scrape(search_url, timeout=15)
        if result.success:
            return {
                "success": True,
                "articles": result.data,
                "source": "pryscraper_news",
            }

        return {
            "success": False,
            "error": result.error or "News search unavailable",
            "articles": [],
        }

    async def stats(self) -> dict[str, Any]:
        """Return usage statistics for the circuit breaker."""
        return {
            "circuit_breaker": _pry_cb.stats(),
            "base_url": self.base_url,
            "x402_offline": self._x402_offline,
            "x402_payments_count": len(self._x402_payments),
        }

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
