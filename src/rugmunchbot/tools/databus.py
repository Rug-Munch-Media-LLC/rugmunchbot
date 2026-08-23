"""DataBus client — bot's platform-backed data fallback.

The last rung of the scanner's provider chain:

    free public APIs  →  freemium keyed providers  →  DataBus (RMI backend)

DataBus aggregates 100+ providers behind the backend's internal auth, so a
token the free tier cannot price/scan can still be served from the platform
cache. Every call is fail-open: on any error the caller moves on — DataBus
being down must never degrade a scan below what the free tier already gave.

Endpoints used (all internal-auth via ``backend_headers``):
    GET  /api/v1/databus/fetch/token_price?mint=   — DexScreener-backed price
    GET  /api/v1/databus/wallet/{address}          — wallet analysis
    POST /api/v2/scanner/token/scan                — full risk scan

Short-TTL in-process memoization: repeated scans of the same token (re-scan
button, group chatter) must not re-hit the backend.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi_bot.tools.databus")

_TIMEOUT = 8.0
_CACHE_TTL = 60.0
_CACHE_MAX = 512
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if not hit:
        return None
    ts, val = hit
    if time.monotonic() - ts > _CACHE_TTL:
        _cache.pop(key, None)
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = (time.monotonic(), val)


def clear_cache() -> None:
    _cache.clear()


def _databus_base() -> str:
    """Late import: config resolves BACKEND_URL lazily (env-overridable)."""
    from rugmunchbot.config import BACKEND_URL

    return f"{BACKEND_URL.rstrip('/')}/api/v1/databus"


async def databus_price(mint: str) -> dict[str, Any] | None:
    """Token price via DataBus DexScreener-backed fetch.

    Returns ``{price_usd, change_24h, volume_24h, source}`` or None.
    """
    if not mint:
        return None
    key = f"price:{mint}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        client = await get_shared_client(_TIMEOUT)
        resp = await client.get(
            f"{_databus_base()}/fetch/token_price",
            params={"mint": mint},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.debug("databus_price status=%s mint=%s", resp.status_code, mint[:10])
            return None
        data = resp.json()
        data = data.get("data", data) if isinstance(data, dict) else {}
        pairs = data.get("pairs", []) if isinstance(data, dict) else []
        if not pairs:
            return None
        p = pairs[0]
        out = {
            "price_usd": float(p.get("priceUsd", 0) or 0),
            "change_24h": (p.get("priceChange") or {}).get("h24", 0),
            "volume_24h": float((p.get("volume") or {}).get("h24", 0) or 0),
            "source": "databus",
        }
        _cache_set(key, out)
        return out
    except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
        logger.debug("databus_price failed mint=%s err=%s", mint[:10], exc)
        return None


async def databus_wallet(address: str) -> dict[str, Any] | None:
    """Wallet analysis via DataBus. Returns raw payload dict or None."""
    if not address:
        return None
    key = f"wallet:{address}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        client = await get_shared_client(_TIMEOUT)
        resp = await client.get(
            f"{_databus_base()}/wallet/{address}",
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.debug("databus_wallet status=%s addr=%s", resp.status_code, address[:10])
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        _cache_set(key, data)
        return data
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.debug("databus_wallet failed addr=%s err=%s", address[:10], exc)
        return None


async def databus_token_scan(address: str, chain: str = "ethereum") -> dict[str, Any] | None:
    """Full risk scan via the backend scanner v2 API (DataBus-backed).

    Returns the scan result dict (risk_score, threats, ...) or None.
    Longer timeout — a full scan is a heavy backend operation.
    """
    if not address:
        return None
    key = f"scan:{chain}:{address}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        from rugmunchbot.config import BACKEND_URL, backend_headers

        client = await get_shared_client(30.0)
        resp = await client.post(
            f"{BACKEND_URL.rstrip('/')}/api/v2/scanner/token/scan",
            json={"address": address, "chain": chain},
            headers=backend_headers(),
            timeout=30.0,
        )
        if resp.status_code != 200:
            logger.debug("databus_scan status=%s addr=%s", resp.status_code, address[:10])
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        _cache_set(key, data)
        return data
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.debug("databus_scan failed addr=%s err=%s", address[:10], exc)
        return None


__all__ = [
    "clear_cache",
    "databus_price",
    "databus_token_scan",
    "databus_wallet",
]
