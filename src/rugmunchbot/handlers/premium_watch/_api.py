"""Thin client for the premium watch endpoints (``/api/v1/watch*``).

The backend is the single source of truth for watches: the same Redis hash
that the cycle diffs is the one this client writes, so the bot never keeps a
second list that could drift. Every call sends the bot key so a
middleware-protected deployment behaves like the open one.

Failures raise :class:`PremiumWatchError` carrying the backend's ``detail`` —
a handler replies with that sentence instead of inventing one.
"""

from __future__ import annotations

import contextlib

import httpx

from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi_bot.premium_watch")
WATCH_PATH = "/api/v1/watch"
EXPORT_PATH = "/api/v1/scan/{token}.json"
TIMEOUT = 20.0


class PremiumWatchError(RuntimeError):
    """A backend refusal, with the status code kept for the handler."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


async def _call(method: str, path: str, **kwargs: object) -> dict:
    """One request, one error type; JSON body returned as a dict."""
    client = get_shared_client(timeout=TIMEOUT)
    try:
        response = await client.request(
            method, f"{BACKEND_URL}{path}", headers=backend_headers(), timeout=TIMEOUT, **kwargs
        )
    except httpx.HTTPError as exc:
        raise PremiumWatchError(f"backend unreachable ({type(exc).__name__})") from exc
    if response.status_code >= 400:
        detail = response.text[:200]
        with contextlib.suppress(ValueError):
            detail = str(response.json().get("detail") or detail)
        raise PremiumWatchError(detail, response.status_code)
    return response.json()


async def watch_token(token: str, chain: str, chat_id: str) -> dict:
    """Subscribe this chat; idempotent, so a re-run reports the same state."""
    return await _call("POST", WATCH_PATH, json={"token": token, "chain": chain, "chat_id": chat_id})


async def unwatch_token(token: str, chain: str, chat_id: str) -> dict:
    """Remove one subscription and report whether it existed."""
    params = {"token": token, "chain": chain, "chat_id": chat_id}
    return await _call("DELETE", WATCH_PATH, params=params)


async def list_watches(chat_id: str) -> list[dict]:
    """This chat's watches, as the backend lists them."""
    body = await _call("GET", WATCH_PATH, params={"chat_id": chat_id})
    rows = body.get("watches") or []
    return [row for row in rows if isinstance(row, dict)]


async def export_scan(token: str, chain: str) -> bytes:
    """The canonical scan export, exactly as the webhook body would carry it."""
    client = get_shared_client(timeout=TIMEOUT)
    try:
        response = await client.get(
            f"{BACKEND_URL}{EXPORT_PATH.format(token=token)}",
            params={"chain": chain},
            headers=backend_headers(),
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise PremiumWatchError(f"backend unreachable ({type(exc).__name__})") from exc
    if response.status_code >= 400:
        raise PremiumWatchError(response.text[:200], response.status_code)
    return response.content
