"""Thin shill-intelligence client — the bot consumes the backend API.

Shill-campaign intelligence moved OUT of the bot and INTO the backend
(``app/domains/shill`` + ``app/routers/shill.py``). This module is the bot's
thin client: it calls ``/api/v1/shill/*`` so the bot stops holding its own
copy of the discovery / evidence / probe logic.

Every function is fail-open (returns ``None`` / ``[]`` / ``{}`` on any error
or an unreachable backend) so the channel ticks degrade to the existing local
fallback rail instead of crashing. The ApiPass key never leaves the backend —
the bot never constructs an ApiPass call here; it asks the backend to probe.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from rugmunchbot.config import _resolve_backend_url, backend_headers
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.services.shill_api")

#: Per-call client timeout (seconds). Backend is on the same host/tailnet.
_TIMEOUT = 20.0


def _url(path: str) -> str:
    return f"{_resolve_backend_url()}{path}"


def _headers() -> dict[str, str]:
    try:
        return backend_headers()
    except Exception:  # fail-open — no key is fine for the public GETs
        return {}


async def _get_json(path: str) -> Any | None:
    """GET *path*, return parsed JSON or None on any failure (fail-open)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_url(path), headers=_headers())
            if r.status_code != 200:
                logger.debug("shill_api:get_non200 path=%s status=%s", path, r.status_code)
                return None
            return r.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.debug("shill_api:get_failed path=%s err=%s", path, str(exc)[:120])
        return None


async def _post_json(path: str, payload: dict[str, Any]) -> Any | None:
    """POST *payload* to *path*, return parsed JSON or None (fail-open)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(_url(path), json=payload, headers=_headers())
            if r.status_code != 200:
                logger.debug("shill_api:post_non200 path=%s status=%s", path, r.status_code)
                return None
            return r.json()
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        logger.debug("shill_api:post_failed path=%s err=%s", path, str(exc)[:120])
        return None


async def health() -> dict[str, Any] | None:
    """Backend shill-service health (None when unreachable)."""
    return await _get_json("/api/v1/shill/health")


async def list_campaigns(
    token: str = "", chain: str = "", limit: int = 50
) -> dict[str, Any] | None:
    """List shill campaigns (evidence-backed)."""
    params = f"?limit={int(limit)}"
    if token:
        params += f"&token={token}"
    if chain:
        params += f"&chain={chain}"
    return await _get_json(f"/api/v1/shill/campaigns{params}")


async def detect_campaigns(
    articles: list[dict[str, Any]],
    *,
    window_hours: int | None = None,
    min_accounts: int | None = None,
    min_mentions: int | None = None,
) -> list[dict[str, Any]]:
    """Post articles to the backend and return detected campaigns."""
    payload: dict[str, Any] = {"articles": articles or []}
    if window_hours is not None:
        payload["window_hours"] = window_hours
    if min_accounts is not None:
        payload["min_accounts"] = min_accounts
    if min_mentions is not None:
        payload["min_mentions"] = min_mentions
    data = await _post_json("/api/v1/shill/campaigns/detect", payload)
    if not isinstance(data, dict):
        return []
    return data.get("campaigns") or []


async def probe_handle(handle: str) -> dict[str, Any] | None:
    """Probe an X handle via the backend ApiPass (rich profile)."""
    h = str(handle or "").strip().lstrip("@")
    if not h:
        return None
    return await _get_json(f"/api/v1/shill/probe/{h}")


async def get_evidence(
    token: str = "", chain: str = "", account: str = "", limit: int = 200
) -> dict[str, Any] | None:
    """Get shill evidence rows."""
    params = f"?limit={int(limit)}"
    if token:
        params += f"&token={token}"
    if chain:
        params += f"&chain={chain}"
    if account:
        params += f"&account={account}"
    return await _get_json(f"/api/v1/shill/evidence{params}")


async def add_evidence(record: dict[str, Any]) -> dict[str, Any] | None:
    """Persist one shill evidence record (backend Qdrant + DB mirror)."""
    return await _post_json("/api/v1/shill/evidence", record or {})


async def wall_of_shame_feed(severity: str = "") -> dict[str, Any] | None:
    """Wall-of-Shame / shill feed (reused backend store)."""
    params = f"?severity={severity}" if severity else ""
    return await _get_json(f"/api/v1/shill/feed{params}")


def health_sync() -> dict[str, Any] | None:
    """Sync wrapper (``asyncio``-free call sites)."""
    try:
        return asyncio.get_event_loop().run_until_complete(health())
    except (RuntimeError, OSError):
        return None


__all__ = [
    "add_evidence",
    "detect_campaigns",
    "get_evidence",
    "health",
    "health_sync",
    "list_campaigns",
    "probe_handle",
    "wall_of_shame_feed",
]
