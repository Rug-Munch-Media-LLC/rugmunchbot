"""Telegram user → backend user linking + user-scoped API client.

The backend keeps a per-Telegram-user account (``tg:<id>``) in Redis
(``rmi:users:telegram``) and mints a per-user JWT through
``POST /api/v1/auth/telegram`` (app/domains/auth/routes_telegram.py). That
route validates Telegram WebApp-style auth data — HMAC-SHA256 over the
sorted ``data_check`` string keyed with ``sha256(BOT_TOKEN)`` — which the
bot can mint itself because it owns BOT_TOKEN. No browser handshake needed.

JWTs are cached in-process for ~6 days (backend expiry is 7 days) so each
command does at most one auth round-trip. All user-scoped calls send BOTH
the per-user ``Authorization: Bearer`` (satisfies ``verify_session``) and
the bot's ``X-API-Key`` (satisfies the auth middleware).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx

from rugmunchbot.config import BACKEND_URL, BOT_TOKEN, backend_headers
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import BackendApiError, get_shared_client

logger = get_logger("rmi_bot.backend_user")

# Backend JWTs expire after 7 days (app/domains/auth/jwt.py JWT_EXPIRY_DAYS);
# refresh a day early so a long-lived bot process never sends a stale token.
_JWT_TTL = 6 * 24 * 3600
_JWT_CACHE: dict[int, tuple[str, float]] = {}


def _telegram_auth_payload(
    user_id: int, username: str | None = None, first_name: str | None = None
) -> dict[str, Any]:
    """Build Telegram WebApp-style auth data the backend's /auth/telegram accepts.

    Mirrors the backend's data-check algorithm exactly: sorted ``k=v`` lines
    joined with newlines, HMAC-SHA256 keyed with ``sha256(bot_token)``.
    """
    data_check: dict[str, str] = {
        "id": str(user_id),
        "auth_date": str(int(time.time())),
    }
    if first_name:
        data_check["first_name"] = first_name
    if username:
        data_check["username"] = username

    sorted_data = "\n".join(f"{k}={v}" for k, v in sorted(data_check.items()))
    secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    data_check["hash"] = hmac.new(secret, sorted_data.encode(), hashlib.sha256).hexdigest()
    return {
        "id": user_id,
        "auth_date": int(data_check["auth_date"]),
        "hash": data_check["hash"],
        **({"first_name": first_name} if first_name else {}),
        **({"username": username} if username else {}),
    }


async def get_backend_token(
    user_id: int, username: str | None = None, first_name: str | None = None
) -> str | None:
    """Return a backend JWT for the Telegram user, minting one if needed.

    Returns ``None`` when the backend cannot be reached or rejects the auth
    data — callers must degrade gracefully (never crash the command).
    """
    cached = _JWT_CACHE.get(user_id)
    if cached and cached[1] > time.time():
        return cached[0]
    try:
        client = get_shared_client(10)
        resp = await client.post(
            f"{BACKEND_URL}/api/v1/auth/telegram",
            json=_telegram_auth_payload(user_id, username, first_name),
            headers=backend_headers(),
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(
                "backend_link_failed user=%s status=%s body=%s",
                user_id,
                resp.status_code,
                resp.text[:200],
            )
            return None
        token = resp.json().get("access_token")
        if not token:
            logger.warning("backend_link_no_token user=%s", user_id)
            return None
        _JWT_CACHE[user_id] = (token, time.time() + _JWT_TTL)
        logger.info("backend_linked user=%s", user_id)
        return token
    except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
        logger.warning("backend_link_error user=%s: %s", user_id, e)
        return None


async def user_api_headers(
    user_id: int, username: str | None = None, first_name: str | None = None
) -> dict[str, str] | None:
    """Auth headers for a user-scoped backend call, or ``None`` when unlinkable."""
    token = await get_backend_token(user_id, username, first_name)
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", **backend_headers()}


# ── User-scoped CRUD wrappers (portfolio + watchlist) ────────────────────


async def _user_request(
    method: str,
    path: str,
    user_id: int,
    username: str | None,
    first_name: str | None,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One authenticated backend call. Raises BackendApiError on failure."""
    headers = await user_api_headers(user_id, username, first_name)
    if headers is None:
        raise BackendApiError(
            "Could not link your Telegram account to the backend", status_code=503, url=path
        )
    try:
        client = get_shared_client(10)
        resp = await client.request(
            method, f"{BACKEND_URL}{path}", json=json_body, headers=headers, timeout=10
        )
    except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
        raise BackendApiError(f"Backend unreachable: {e}", status_code=0, url=path) from e
    if resp.status_code not in (200, 201):
        raise BackendApiError(
            f"HTTP {resp.status_code} from {path}: {resp.text[:160]}",
            status_code=resp.status_code,
            url=path,
        )
    body = resp.json()
    return body if isinstance(body, dict) else {}


async def get_portfolio(
    user_id: int, username: str | None = None, first_name: str | None = None
) -> dict[str, Any]:
    """GET /api/v1/portfolio/ — ``{"entries": [...], "count": n}``."""
    return await _user_request("GET", "/api/v1/portfolio/", user_id, username, first_name)


async def get_watchlist(
    user_id: int, username: str | None = None, first_name: str | None = None
) -> dict[str, Any]:
    """GET /api/v1/watchlist/ — ``{"entries": [...], "count": n}``."""
    return await _user_request("GET", "/api/v1/watchlist/", user_id, username, first_name)


async def add_watchlist(
    user_id: int,
    symbol: str,
    chain: str,
    address: str | None,
    username: str | None = None,
    first_name: str | None = None,
) -> dict[str, Any]:
    """POST /api/v1/watchlist/ — upsert on symbol+chain."""
    return await _user_request(
        "POST",
        "/api/v1/watchlist/",
        user_id,
        username,
        first_name,
        json_body={"symbol": symbol, "chain": chain, "address": address},
    )


async def remove_watchlist(
    user_id: int, entry_id: str, username: str | None = None, first_name: str | None = None
) -> dict[str, Any]:
    """DELETE /api/v1/watchlist/{entry_id}."""
    return await _user_request(
        "DELETE", f"/api/v1/watchlist/{entry_id}", user_id, username, first_name
    )


async def set_watchlist_alert(
    user_id: int,
    entry_id: str,
    direction: str,
    price: float,
    username: str | None = None,
    first_name: str | None = None,
) -> dict[str, Any]:
    """PUT /api/v1/watchlist/{entry_id} — set alert_above or alert_below."""
    field = "alert_above" if direction == "above" else "alert_below"
    return await _user_request(
        "PUT",
        f"/api/v1/watchlist/{entry_id}",
        user_id,
        username,
        first_name,
        json_body={field: price},
    )


__all__ = [
    "add_watchlist",
    "get_backend_token",
    "get_portfolio",
    "get_watchlist",
    "remove_watchlist",
    "set_watchlist_alert",
    "user_api_headers",
]
