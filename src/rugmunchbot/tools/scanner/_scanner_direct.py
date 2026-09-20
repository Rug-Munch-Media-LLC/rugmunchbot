"""Direct scanner-service client — feature-flagged (``SCANNER_DIRECT``).

Cutover batch-4. ``rugmunch-scanner`` now runs standalone (batch-2 ``serve``
runtime exposing ``/healthz`` + ``/readyz`` — ``rugmunch-scanner/docs/
STANDALONE.md``). This module lets the bot call that service directly instead
of routing every scan through rmi-backend.

Contract
========
* Health: ``GET {base}/healthz`` (liveness) then ``GET {base}/readyz``
  (readiness — 503 when the service is not ready). A scan is attempted only
  when BOTH answer 200.
* Token scan: ``POST {base}/api/v2/scanner/token/scan`` ``{address, chain}``
  (the same v2 shape ``tools/scanner/_backend.py`` posts); ``deep=True`` adds
  the ``?depth=deep`` query documented in ``docs/TELEGRAM-BOT-V3-DESIGN.md``.
* Wallet scan: ``POST {base}/api/v2/scanner/wallet/scan`` ``{address, chain}``
  (mirrors ``_scans.analyze_wallet_v2``).

.. note::
   The standalone runtime currently serves ONLY the two probes — the v2 scan
   routes land with the batch-3 scan rail (``STANDALONE.md`` §TODO). Until
   then a flag-on call health-gates, receives 404 on the scan route, and
   fails open (``None``) to the existing backend/local chain.

Flag / config — read at call time, no restart to flip
=====================================================
* ``SCANNER_DIRECT``      — ``1``/``true``/``yes``/``on`` enables the client
  (default ``"0"`` = OFF, inert). Flipping it never changes behaviour on its
  own: the service URL must also be set and reachable.
* ``SCANNER_SERVICE_URL`` — base URL of the scanner service, e.g.
  ``http://127.0.0.1:8098``. ``SCANNER_DIRECT_URL`` is accepted as a legacy
  alias; ``SCANNER_SERVICE_URL`` wins when both are set.

Every entry point returns ``None`` on any failure (flag off, URL unset,
service unreachable, not-ready, non-200, non-``ok`` payload) so callers
degrade to the next leg of the existing fallback chain — it never raises and
never blocks a user scan. Mirrors ``tools/scanner/_backend.py``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.scanner.direct")

SCANNER_DIRECT_ENV = "SCANNER_DIRECT"
SCANNER_SERVICE_URL_ENV = "SCANNER_SERVICE_URL"
SCANNER_DIRECT_URL_ENV = "SCANNER_DIRECT_URL"

HEALTH_PATH = "/healthz"
READY_PATH = "/readyz"
V2_SCAN_PATH = "/api/v2/scanner/token/scan"
V2_WALLET_SCAN_PATH = "/api/v2/scanner/wallet/scan"

#: Probe budget — a slow probe must never delay a user scan.
HEALTH_TIMEOUT = 3.0
#: Hard scan cap, matching the backend scan budget (unified engine ~25s).
SCAN_TIMEOUT = 45.0

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def scanner_direct_enabled() -> bool:
    """True when the direct-scanner client is switched on (default off)."""
    return os.getenv(SCANNER_DIRECT_ENV, "0").strip().lower() in _TRUTHY


def scanner_direct_url() -> str | None:
    """Base URL of the scanner service, or ``None`` when unset.

    ``SCANNER_SERVICE_URL`` is canonical; ``SCANNER_DIRECT_URL`` remains a
    backward-compatible alias so an existing deployment keeps working.
    """
    for key in (SCANNER_SERVICE_URL_ENV, SCANNER_DIRECT_URL_ENV):
        url = os.getenv(key, "").strip()
        if url:
            return url.rstrip("/")
    return None


async def _probe(client: httpx.AsyncClient, path: str) -> int:
    """GET ``path``; return its status code, or ``0`` when unreachable."""
    try:
        return (await client.get(path)).status_code
    except httpx.HTTPError as exc:
        logger.warning("scanner_direct_probe_failed path=%s err=%s", path, str(exc)[:120])
        return 0


async def scanner_service_ready(base_url: str | None = None) -> bool:
    """True when the service answers liveness AND readiness with 200."""
    url = base_url or scanner_direct_url()
    if url is None:
        return False
    try:
        async with httpx.AsyncClient(base_url=url, timeout=HEALTH_TIMEOUT) as client:
            if await _probe(client, HEALTH_PATH) != 200:
                return False
            ready = await _probe(client, READY_PATH)
    except Exception as exc:  # fail-open boundary: never raise to a scan
        logger.warning("scanner_direct_health_error url=%s err=%s", url, str(exc)[:120])
        return False
    if ready != 200:
        logger.info("scanner_direct_not_ready url=%s status=%s", url, ready)
        return False
    return True


async def _post_scan(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    params: dict[str, str] | None,
) -> dict[str, Any] | None:
    """POST a scan; return the payload only when it is a 200 ``status == ok``."""
    try:
        resp = await client.post(path, json=payload, params=params)
    except httpx.HTTPError as exc:
        logger.warning("scanner_direct_scan_failed path=%s err=%s", path, str(exc)[:120])
        return None
    if resp.status_code != 200:
        logger.warning("scanner_direct_scan_http path=%s status=%s", path, resp.status_code)
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.warning("scanner_direct_scan_bad_json path=%s", path)
        return None
    if not isinstance(data, dict) or data.get("status") != "ok":
        logger.warning("scanner_direct_scan_non_ok path=%s", path)
        return None
    return data


async def _direct_scan(
    path: str, payload: dict[str, Any], *, params: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Readiness-gate then POST ``path``; ``None`` on any failure (fail-open)."""
    url = scanner_direct_url()
    if url is None:
        logger.warning("scanner_direct_enabled_without_url path=%s", path)
        return None
    try:
        async with httpx.AsyncClient(base_url=url, timeout=SCAN_TIMEOUT) as client:
            if await _probe(client, READY_PATH) != 200:
                logger.info("scanner_direct_unready_fallback path=%s url=%s", path, url)
                return None
            return await _post_scan(client, path, payload, params)
    except Exception as exc:  # fail-open boundary: never break a user scan
        logger.warning("scanner_direct_error path=%s err=%s", path, str(exc)[:120])
        return None


async def direct_scan_token(
    address: str, chain: str, *, deep: bool = False
) -> dict[str, Any] | None:
    """Token scan via the standalone service — ``None`` when off/unusable."""
    if not scanner_direct_enabled():
        return None
    params = {"depth": "deep"} if deep else None
    return await _direct_scan(V2_SCAN_PATH, {"address": address, "chain": chain}, params=params)


async def direct_scan_wallet(address: str, chain: str) -> dict[str, Any] | None:
    """Wallet forensics via the standalone service — ``None`` when off/unusable."""
    if not scanner_direct_enabled():
        return None
    return await _direct_scan(V2_WALLET_SCAN_PATH, {"address": address, "chain": chain})


__all__ = [
    "HEALTH_PATH",
    "READY_PATH",
    "SCANNER_DIRECT_ENV",
    "SCANNER_DIRECT_URL_ENV",
    "SCANNER_SERVICE_URL_ENV",
    "V2_SCAN_PATH",
    "V2_WALLET_SCAN_PATH",
    "direct_scan_token",
    "direct_scan_wallet",
    "scanner_direct_enabled",
    "scanner_direct_url",
    "scanner_service_ready",
]
