# VENDORED 2026-08-17 from rmi-backend/app/databus/_gmgn_fallbacks.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""GMGN fallback + cache helpers — leaf module for GMGNProvider.

Extracted from ``app.databus.gmgn_provider`` during the de-godding split
(2026-08-16). Module-level async helpers that take a ``request`` callable
(bound ``GMGNProvider._request``) so the class stays the public facade while
the leaf logic (DexScreener/Birdeye fallbacks, trade normalization, Redis
cache) lives here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("databus.gmgn_provider")

#: Type of the provider's ``_request`` bound method.
RequestFunc = Callable[..., Awaitable[httpx.Response]]


def agent_auth_params(api_key: str, **extra: Any) -> dict[str, Any]:
    """Build the Agent API auth query params (timestamp + client_id UUID).

    Mirrors buildAuthQuery() in the official gmgn-skills SDK. The server
    validates ``timestamp`` within ±5s and rejects ``client_id`` replays
    within 7s, so both must be fresh per request.
    """
    import uuid

    params: dict[str, Any] = {"timestamp": int(datetime.now(UTC).timestamp()), "client_id": str(uuid.uuid4())}
    params.update(extra)
    return params


def agent_headers(api_key: str) -> dict[str, str]:
    """Headers for the Agent API: X-APIKEY (exist auth) + JSON content type."""
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "user-agent": "gmgn-provider/1.0",
    }
    if api_key:
        headers["X-APIKEY"] = api_key
    return headers


async def fallback_dexscreener_token(request: RequestFunc, address: str, chain: str) -> dict[str, Any]:
    """Fallback token info from DexScreener (free, no key)."""
    try:
        resp = await request(
            "GET",
            f"https://api.dexscreener.com/latest/dex/tokens/{address}",
        )
        if resp.status_code == 200:
            pairs = resp.json().get("pairs", [])
            if pairs:
                p = pairs[0]
                return {
                    "address": address,
                    "chain": p.get("chainId", chain),
                    "name": p.get("baseToken", {}).get("name", ""),
                    "symbol": p.get("baseToken", {}).get("symbol", ""),
                    "price": float(p.get("priceUsd", 0)),
                    "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                    "fdv": float(p.get("fdv", 0)),
                    "market_cap": float(p.get("marketCap", 0)),
                    "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                    "price_change_1h": float(p.get("priceChange", {}).get("h1", 0)),
                    "price_change_24h": float(p.get("priceChange", {}).get("h24", 0)),
                    "dex": p.get("dexId", ""),
                    "pair_created_at": p.get("pairCreatedAt"),
                    "source": "dexscreener_fallback",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.debug("DexScreener fallback failed: %s", e)
    return {"address": address, "error": "no_data", "source": "none"}


async def fallback_dexscreener_trades(
    request: RequestFunc, address: str, chain: str, limit: int
) -> list[dict[str, Any]]:
    """Fallback: Derive trade summary from DexScreener pair data (io endpoint blocked by CF)."""
    try:
        import time

        resp = await request(
            "GET",
            f"https://api.dexscreener.com/latest/dex/tokens/{address}",
        )
        if resp.status_code == 200:
            pairs = resp.json().get("pairs", [])
            if pairs:
                p = pairs[0]
                vol_24h = float(p.get("volume", {}).get("h24", 0))
                buys = int(p.get("txns", {}).get("h24", {}).get("buys", 0))
                sells = int(p.get("txns", {}).get("h24", {}).get("sells", 0))
                price = float(p.get("priceUsd", 0))
                price_change = float(p.get("priceChange", {}).get("h24", 0))
                total = buys + sells
                if total > 0 and vol_24h > 0:
                    avg_trade = vol_24h / total
                    now_ts = int(time.time() * 1000)
                    trades = []
                    for i in range(min(limit, total)):
                        is_buy = buys > sells if i == 0 else (i % 2 == 0)
                        trades.append(
                            {
                                "tx_hash": f"dex:{p.get('pairAddress', address)}:{i}",
                                "timestamp": int(now_ts - (i * 60000 / max(total, 1))),
                                "side": "buy" if is_buy else "sell",
                                "price_usd": round(price * (1 + (price_change / 100) * (i / max(total, 1))), 10),
                                "amount_usd": round(avg_trade, 2),
                                "amount_token": round(avg_trade / price, 6) if price > 0 else 0,
                                "maker": "",
                                "source": "dexscreener_derived",
                            }
                        )
                    return trades
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.debug("DexScreener trades fallback failed: %s", e)
    return []


async def fallback_dexscreener_new_pairs(request: RequestFunc, chain: str, limit: int) -> list[dict[str, Any]]:
    """Fallback new pairs from DexScreener search."""
    try:
        resp = await request(
            "GET",
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": chain.upper() if chain != "solana" else "SOL", "limit": limit},
        )
        if resp.status_code == 200:
            pairs = resp.json().get("pairs", [])
            return [
                {
                    "address": p.get("baseToken", {}).get("address", ""),
                    "chain": p.get("chainId", chain),
                    "symbol": p.get("baseToken", {}).get("symbol", ""),
                    "name": p.get("baseToken", {}).get("name", ""),
                    "price": float(p.get("priceUsd", 0)),
                    "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                    "market_cap": float(p.get("fdv", 0)),
                    "created_at": p.get("pairCreatedAt"),
                    "source": "dexscreener_fallback",
                }
                for p in pairs
                if float(p.get("liquidity", {}).get("usd", 0)) > 100
            ]
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.debug("DexScreener new pairs fallback failed: %s", e)
    return []


async def fallback_birdeye_holders(request: RequestFunc, address: str) -> dict[str, Any]:
    """Fallback holders from Birdeye."""
    try:
        api_key = os.getenv("BIRDEYE_API_KEY", "")
        if not api_key:
            return {"address": address, "total_holders": 0, "source": "none"}
        resp = await request(
            "GET",
            "https://public-api.birdeye.so/defi/token_overview",
            headers={"X-API-KEY": api_key, "accept": "application/json"},
            params={"address": address},
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return {
                "address": address,
                "total_holders": data.get("holder", 0),
                "top_10_pct": data.get("top10HolderPercent", 0),
                "source": "birdeye_fallback",
                "timestamp": datetime.now(UTC).isoformat(),
            }
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.debug("Birdeye holders fallback failed: %s", e)
    return {"address": address, "total_holders": 0, "source": "none"}


def normalize_trades(trades: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Normalize trade data from any source to a standard format."""
    normalized = []
    for t in trades:
        amount_usd = float(t.get("volume", {}).get("usd", t.get("amountUsd", t.get("usd_amount", 0))))
        normalized.append(
            {
                "tx_hash": t.get("txHash", t.get("tx_hash", "")),
                "timestamp": t.get("time", t.get("timestamp", t.get("blockTime", ""))),
                "side": "buy" if t.get("type", "").lower() in ("buy", "b") else "sell",
                "price_usd": float(t.get("priceUsd", t.get("price", 0))),
                "amount_usd": amount_usd,
                "amount_token": float(t.get("amount", t.get("token_amount", 0))),
                "maker": t.get("maker", t.get("wallet", "")),
                "is_whale": amount_usd > 10000,
                "source": source,
            }
        )
    return normalized


async def cached_or_fetch(
    cache_key: str,
    ttl: int,
    fetcher: Any,
    *args: Any,
) -> Any:
    """Try Redis cache first, then fetch. Cache result on success."""
    # Try Redis
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r:
            cached = await r.get(f"gmgn:{cache_key}")
            if cached:
                return json.loads(cached)
    except Exception:  # noqa: S110 — best-effort cache
        pass

    # Fetch
    result = await fetcher(*args)
    if result:
        # Cache in Redis
        try:
            from rugmunchbot._vendor.redis_client import get_redis_async

            r = get_redis_async()
            if r:
                await r.setex(f"gmgn:{cache_key}", ttl, json.dumps(result))
        except Exception:  # noqa: S110 — best-effort cache
            pass

    return result


async def cached_or_fetch_list(
    cache_key: str,
    ttl: int,
    fetcher: Any,
    *args: Any,
) -> list[dict[str, Any]]:
    """Same as cached_or_fetch but for list returns."""
    return await cached_or_fetch(cache_key, ttl, fetcher, *args) or []
