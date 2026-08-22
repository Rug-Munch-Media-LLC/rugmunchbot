# VENDORED 2026-08-17 from rmi-backend/app/databus/rugcharts_intel/smart_money.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
RugCharts Intel — Smart Money Feed.
Tracks what the most profitable wallets are buying right now.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.cache import ARKHAM_KEY, CACHE_TTL, HELIUS_KEY, _r
from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("rugcharts_intel")


async def smart_money_feed(chain: str = "solana", limit: int = 20, **kw) -> dict[str, Any] | None:
    """Track what the most profitable wallets are buying right now.

    Uses: Helius recent transactions + Arkham entity labels + local wallet scoring.
    """
    cache_key = f"smart_money:{chain}:{limit}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    smart_wallets = []
    api_key = kw.get("api_key", "") or HELIUS_KEY

    try:
        # Pull from our known smart money wallet set
        # For now: query high-value recent transactions
        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=15) as c:
                # Get recent program transactions (Raydium/Jupiter swaps)
                # Look for wallets with profitable patterns
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getRecentPrioritizationFees",
                        "params": [["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"]],
                    },
                )

                # Fallback: use top token holder data
                r2 = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenLargestAccounts",
                        "params": ["So11111111111111111111111111111111111111112"],
                    },
                )
                if r2.status_code == 200:
                    holders = r2.json().get("result", {}).get("value", [])[:30]
                    for h in holders:
                        addr = h.get("address", "")
                        amount = float(h.get("amount", 0)) / 1e9
                        if amount > 100:  # >$10K SOL
                            smart_wallets.append(
                                {
                                    "address": addr,
                                    "balance_sol": round(amount, 2),
                                    "estimated_usd": round(amount * 150, 0),  # approximate
                                    "signal": "large_holder",
                                }
                            )
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Smart money feed: {e}")

    # Enrich with Arkham entity labels
    if ARKHAM_KEY and smart_wallets:
        for w in smart_wallets[:10]:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    ar = await c.get(
                        f"https://api.arkhamintelligence.com/intelligence/address/{w['address']}",
                        headers={"API-Key": ARKHAM_KEY},
                    )
                    if ar.status_code == 200:
                        entity = ar.json().get("arkhamEntity", {})
                        w["entity"] = entity.get("name", "")
                        w["entity_type"] = entity.get("type", "")
            except (ValueError, ConnectionError, TimeoutError, RuntimeError):
                logger.warning("error")

    result = {
        "smart_money_wallets": smart_wallets[:limit],
        "total_tracked": len(smart_wallets),
        "chain": chain,
        "scan_time": datetime.now(UTC).isoformat(),
        "source": "smart_money_feed",
    }

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["smart_money"], json.dumps(result, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return result
