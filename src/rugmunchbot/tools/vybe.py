"""Vybe Network client — labeled Solana on-chain intelligence.

Free tier: 25K credits/mo, 60 RPM, historical data to 48h. Base URL
``https://api.vybenetwork.xyz`` with ``X-API-Key`` auth. NOTE: paths are
versioned (``/v4/...``) — the guide examples omit the prefix and 404.

What Vybe adds that our other providers don't: **entity-labeled** top
holders (CEX / KOL / VC / treasury tags) and wallet PnL — the smart-money
signals behind a token's holder distribution.

Every call is fail-open (None on any error); short-TTL memoization keeps
repeat scans of the same mint from burning credits.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi_bot.tools.vybe")

_BASE = os.getenv("VYBE_BASE_URL", "https://api.vybenetwork.xyz")
_TIMEOUT = 8.0
_CACHE_TTL = 300.0  # holder data refreshes every 3h upstream; 5min is plenty
_cache: dict[str, tuple[float, Any]] = {}


def _api_key() -> str:
    return os.getenv("VYBE_API_KEY", "")


def clear_cache() -> None:
    _cache.clear()


async def _get(path: str, params: dict[str, Any] | None = None) -> Any | None:
    key = path + repr(sorted((params or {}).items()))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    api_key = _api_key()
    if not api_key:
        return None
    try:
        client = get_shared_client(_TIMEOUT)
        resp = await client.get(
            f"{_BASE}{path}",
            params=params,
            headers={"X-API-Key": api_key},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.debug("vybe status=%s path=%s", resp.status_code, path[:40])
            return None
        data = resp.json()
        _cache[key] = (time.monotonic(), data)
        return data
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.debug("vybe failed path=%s err=%s", path[:40], exc)
        return None


async def vybe_token_details(mint: str) -> dict[str, Any] | None:
    """Token details: price, mcap, FDV, holders count, supply, socials."""
    if not mint:
        return None
    data = await _get(f"/v4/tokens/{mint}")
    return data if isinstance(data, dict) else None


async def vybe_top_holders(mint: str, limit: int = 10) -> list[dict[str, Any]]:
    """Top holders with entity labels (CEX/KOL/VC/treasury).

    Returns list of {address, owner_name, pct, usd_value, label}.
    """
    if not mint:
        return []
    data = await _get(f"/v4/tokens/{mint}/top-holders", {"limit": min(max(limit, 1), 50)})
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows[:limit]:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "address": r.get("ownerAddress") or "",
                "owner_name": r.get("ownerName") or "",
                "pct": float(r.get("percentageOfSupplyHeld") or 0),
                "usd_value": float(r.get("valueUsd") or 0),
                "label": r.get("ownerLabel") or r.get("ownerCategory") or "",
            }
        )
    return out


async def vybe_wallet_pnl(wallet: str) -> dict[str, Any] | None:
    """Wallet PnL summary: realized/unrealized, win rate, volume."""
    if not wallet:
        return None
    data = await _get(f"/v4/wallets/{wallet}/pnl")
    return data if isinstance(data, dict) else None


__all__ = [
    "clear_cache",
    "vybe_token_details",
    "vybe_top_holders",
    "vybe_wallet_pnl",
]
