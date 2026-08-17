#!/usr/bin/env python3
"""
RugMunchBot -- /trending source fetchers.

Multi-source trending fetchers (RugCharts + DexScreener boosts + fresh-launch
profiles) plus the quick-scan enrichment step. Split out of
``handlers/market.py`` (2026-08-16). ``render_trending`` and the fused-list
Redis cache stay in ``handlers.market`` so its cache layer keeps resolving
patchable globals from the market module.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, DEXSCREENER, backend_headers
from app.domains.telegram.rugmunchbot.handlers._market_helpers import _merge_trending_sources
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client, http_get_any, http_get_json

logger = get_logger("rmi_bot")


async def _fetch_cg_trending_news() -> list[dict[str, Any]] | None:
    """Fallback: fetch CoinGecko trending coins as pseudo-news items."""
    body = await http_get_json("https://api.coingecko.com/api/v3/search/trending", timeout=8)
    if not body:
        logger.debug("CoinGecko trending fallback failed")
        return None
    coins = body.get("coins", [])
    if not coins:
        return None
    items = []
    for c in coins[:5]:
        item = c.get("item", {})
        items.append(
            {
                "title": f"{item.get('name', 'Unknown')} ({item.get('symbol', '?')}) trending now",
                "source": "Trending",
                "published_at": None,
                "url": f"https://www.coingecko.com/en/coins/{item.get('id', '')}",
                "score": item.get("score", 0) or 0,
            }
        )
    return items


async def _fetch_dexscreener_trending(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]] | None:
    """Fetch trending from DexScreener token boosts, then enrich with token details."""
    try:
        client = get_shared_client(15)
        # Step 1: Get boosted tokens
        boosted = await http_get_any("https://api.dexscreener.com/token-boosts/top/v1", timeout=15)
        if not isinstance(boosted, list):
            return None

        # Step 2: Fetch token details for each boosted token (parallel, max 10)
        semaphore = asyncio.Semaphore(10)

        async def fetch_token_details(item: dict[str, Any]) -> dict[str, Any] | None:
            """Enrich one boosted-token entry with live pair data; None on failure."""
            if not isinstance(item, dict):
                return None
            token_addr = item.get("tokenAddress") or item.get("address")
            if not token_addr:
                return None
            # Filter by chain if specified
            item_chain = item.get("chainId", "").lower()
            if chain and chain != "all" and item_chain != chain.lower():
                return None

            async with semaphore:
                try:
                    r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}", timeout=8)
                    if r.status_code != 200:
                        return None
                    data = r.json()
                    pairs = data.get("pairs", [])
                    if not pairs:
                        return None
                    p = pairs[0]
                    bt = p.get("baseToken", {})
                    name = bt.get("name") or "Unknown"
                    symbol = bt.get("symbol") or "???"
                    # Skip obvious garbage and low-quality tokens
                    garbage_patterns = [
                        "crawled",
                        "smol",
                        "anaem",
                        "dev leave",
                        "robinhood",
                        "mow",
                        "pengu mow",  # known low-quality meme tokens
                    ]
                    if any(g in name.lower() for g in garbage_patterns):
                        return None
                    if any(g in symbol.lower() for g in garbage_patterns):
                        return None
                    # Minimum quality thresholds
                    liquidity = float(p.get("liquidity", {}).get("usd") or 0)
                    volume_24h = float(p.get("volume", {}).get("h24") or 0)
                    # Skip tokens with very low liquidity (<$1000) or volume (<$5000)
                    if liquidity < 1000 or volume_24h < 5000:
                        return None
                    return {
                        "address": token_addr,
                        "chain": p.get("chainId", chain),
                        "name": name,
                        "symbol": symbol,
                        "price_usd": float(p.get("priceUsd") or 0),
                        "volume_24h": float(p.get("volume", {}).get("h24") or 0),
                        "liquidity_usd": float(p.get("liquidity", {}).get("usd") or 0),
                        "change_24h": float(p.get("priceChange", {}).get("h24") or 0),
                        "change_1h": float(p.get("priceChange", {}).get("h1") or 0),
                        "change_7d": float(p.get("priceChange", {}).get("d7") or 0),
                        "created_at": p.get("pairCreatedAt"),
                        "dex": p.get("dexId"),
                        "info": p.get("info", {}),
                        "source": "dexscreener",
                    }
                except Exception:
                    return None

        # Fetch details for top boosted tokens
        tasks = [fetch_token_details(item) for item in boosted[: limit * 2]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tokens = [r for r in results if isinstance(r, dict) and r is not None]
        return tokens[:limit] if tokens else None

    except Exception as exc:
        logger.debug("dexscreener trending fetch failed: %s", exc)
        return None


async def _fetch_dexscreener_fresh_launches(chain: str = "solana", limit: int = 12) -> list[dict[str, Any]] | None:
    """Freshly deployed tokens from DexScreener token-profiles (pump.fun etc.).

    ``/token-profiles/latest/v1`` is the same feed the trending algorithm
    uses to seed fresh launches — real ``tokenAddress`` values, mostly
    pump.fun on Solana. Each profile is enriched with live pair data so the
    entry carries a real address, price, 24h change, volume, liquidity and
    creation time (for the fresh-launch age badge).
    """
    try:
        client = get_shared_client(15)
        profiles = await http_get_any("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        if not isinstance(profiles, list) or not profiles:
            return None

        semaphore = asyncio.Semaphore(10)

        async def fetch_profile_details(item: dict[str, Any]) -> dict[str, Any] | None:
            """Enrich one fresh-launch profile with live pair data; None on failure."""
            if not isinstance(item, dict):
                return None
            token_addr = item.get("tokenAddress") or ""
            if not token_addr:
                return None
            item_chain = (item.get("chainId") or "").lower()
            if chain and chain != "all" and item_chain != chain.lower():
                return None

            async with semaphore:
                try:
                    r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}", timeout=8)
                    if r.status_code != 200:
                        return None
                    pairs = r.json().get("pairs", [])
                    if not pairs:
                        return None
                    p = pairs[0]
                    bt = p.get("baseToken", {})
                    name = bt.get("name") or ""
                    symbol = bt.get("symbol") or ""
                    if not name or not symbol:
                        return None
                    return {
                        "address": token_addr,
                        "chain": p.get("chainId", item_chain),
                        "name": name,
                        "symbol": symbol,
                        "price_usd": float(p.get("priceUsd") or 0),
                        "volume_24h": float(p.get("volume", {}).get("h24") or 0),
                        "liquidity_usd": float(p.get("liquidity", {}).get("usd") or 0),
                        "change_24h": float(p.get("priceChange", {}).get("h24") or 0),
                        "change_1h": float(p.get("priceChange", {}).get("h1") or 0),
                        "change_7d": float(p.get("priceChange", {}).get("d7") or 0),
                        "created_at": p.get("pairCreatedAt"),
                        "dex": p.get("dexId"),
                        "source": "dexscreener:profiles",
                        "is_new_launch": True,
                    }
                except Exception:
                    return None

        tasks = [fetch_profile_details(item) for item in profiles[: limit * 2]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tokens = [r for r in results if isinstance(r, dict) and r is not None]
        return tokens[:limit] if tokens else None

    except Exception as exc:
        logger.debug("dexscreener fresh-launch fetch failed: %s", exc)
        return None


async def _fetch_fused_trending(chain: str = "solana", limit: int = 20) -> list[dict[str, Any]] | None:
    """Fetch and fuse trending from degen sources with enrichment.

    Sources (no CoinGecko search-score — it has no contract addresses and is
    dominated by CEX-listed infra tokens nobody trades):
      1. RugCharts backend feed (our own data, real addresses).
      2. DexScreener token-boosts (paid attention, real addresses).
      3. DexScreener token-profiles — pump.fun / newly deployed, real addresses.
    """
    # Fetch from all sources in parallel
    rg_task = _fetch_rugcharts_trending(chain, limit)
    ds_task = _fetch_dexscreener_trending(chain, limit)
    fresh_task = _fetch_dexscreener_fresh_launches(chain, limit)

    results = await asyncio.gather(rg_task, ds_task, fresh_task, return_exceptions=True)
    rugcharts = results[0] if not isinstance(results[0], BaseException) else None
    dexscreener = results[1] if not isinstance(results[1], BaseException) else None
    fresh_launches = results[2] if not isinstance(results[2], BaseException) else None

    # Handle exceptions
    if isinstance(rugcharts, Exception):
        rugcharts = None
    if isinstance(dexscreener, Exception):
        dexscreener = None
    if isinstance(fresh_launches, Exception):
        fresh_launches = None

    # Merge sources (fresh launches = highest priority alongside rugcharts)
    fused = _merge_trending_sources(rugcharts, dexscreener, fresh_launches, limit)

    if not fused:
        return None

    # Enrich with quick risk scans
    enriched = await _enrich_with_quick_scan(fused, chain)

    return enriched


async def _fetch_rugcharts_trending(chain: str = "solana", limit: int = 8) -> list[dict[str, Any]] | None:
    """Fetch trending tokens from the RugCharts backend."""
    try:
        body = await http_get_json(
            f"{BACKEND_URL}/api/v1/rugcharts/trending",
            params={"chain": chain, "limit": limit},
            headers=backend_headers(),
            timeout=15,
        )
        if body is None:
            logger.debug("rugcharts trending status non-200")
            return None
        data = body.get("data") or body
        tokens = data.get("tokens") or []
        for t in tokens:
            t["source"] = "rugcharts"
        return tokens if tokens else None
    except Exception as exc:
        logger.debug("rugcharts trending fetch failed: %s", exc)
        return None


async def _enrich_with_quick_scan(tokens: list[dict[str, Any]], chain: str) -> list[dict[str, Any]]:
    """Add quick risk scan data + DexScreener symbol to trending tokens."""
    if not tokens:
        return tokens

    async def scan_one(token: dict[str, Any]) -> dict[str, Any]:
        """Run a backend quick risk scan on one token; always returns the token."""
        addr = token.get("address")
        if not addr:
            return token
        try:
            client = get_shared_client(10)
            # Fetch risk scan
            r = await client.post(
                f"{BACKEND_URL}/api/v1/bot/scan",
                headers=backend_headers(),
                json={"address": addr, "chain": chain},
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and not data.get("error"):
                    token["risk_score"] = data.get("risk_score")
                    token["holders"] = data.get("holders")
                    token["liquidity_usd"] = data.get("liquidity") or token.get("liquidity_usd")
                    token["bundle_detected"] = data.get("bundle_detected")
            # Fetch symbol from DexScreener if missing
            if not token.get("symbol"):
                try:
                    r2 = await client.get(f"{DEXSCREENER}/tokens/{addr}", timeout=6)
                    if r2.status_code == 200:
                        pairs = r2.json().get("pairs", [])
                        if pairs:
                            p = pairs[0]
                            token["symbol"] = p.get("baseToken", {}).get("symbol", "")
                            if not token.get("name"):
                                token["name"] = p.get("baseToken", {}).get("name", "")
                except Exception:  # noqa: S110 — enrichment is best-effort
                    pass
        except Exception:  # noqa: S110 — return token even if enrichment fails
            pass
        return token

    # Run scans in parallel (max 5 concurrent to avoid overload)
    semaphore = asyncio.Semaphore(5)

    async def limited_scan(t: dict[str, Any]) -> dict[str, Any]:
        """Run :func:`scan_one` under a shared concurrency semaphore."""
        async with semaphore:
            return await scan_one(t)

    enriched = await asyncio.gather(*[limited_scan(t) for t in tokens], return_exceptions=True)
    return [t for t in enriched if isinstance(t, dict)]
