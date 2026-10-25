"""Ankr Advanced multichain client — the whale profiler's only data source.

Single purpose: talk to Ankr and return typed balances/transfers. Every
malformed upstream field degrades to a safe value instead of raising — a
provider sending ``"balanceUsd": "n/a"`` must not blank the whole profile.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ANKR_MULTICHAIN = "https://rpc.ankr.com/multichain"


def usd(value: Any) -> float:
    """Coerce an upstream USD field to float (0.0 on anything malformed)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ankr_key() -> str:
    return os.getenv("ANKR_API_KEY", "").strip()


async def _ankr_call(method: str, params: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    key = _ankr_key()
    if not key:
        raise RuntimeError("ANKR_API_KEY unset")
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(
            ANKR_MULTICHAIN,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            headers={"X-API-KEY": key},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Ankr HTTP {r.status_code}")
        d = r.json()
        if not isinstance(d, dict):
            raise RuntimeError("Ankr returned a non-object body")
        if "error" in d:
            raise RuntimeError(f"Ankr error: {d['error']}")
        result = d.get("result", {})
        return result if isinstance(result, dict) else {}


async def get_wallet_balance(wallet: str) -> dict[str, Any]:
    """Multichain token balances for a wallet (malformed rows skipped)."""
    res = await _ankr_call("ankr_getAccountBalance", {"walletAddress": wallet})
    assets = [a for a in (res.get("assets") or []) if isinstance(a, dict)]
    total_usd = sum(usd(a.get("balanceUsd")) for a in assets)
    tokens = sorted(assets, key=lambda a: usd(a.get("balanceUsd")), reverse=True)
    return {
        "total_usd": round(total_usd, 2),
        "token_count": len(assets),
        "top_tokens": [
            {
                "symbol": a.get("symbol", "?"),
                "chain": a.get("chainId", ""),
                "balance_usd": round(usd(a.get("balanceUsd")), 2),
            }
            for a in tokens[:10]
        ],
    }


async def get_wallet_transfers(wallet: str, limit: int = 20) -> list[dict[str, Any]]:
    """Recent transfers involving this wallet (malformed rows skipped)."""
    res = await _ankr_call(
        "ankr_getTransactionsByAddress",
        {"address": wallet, "pageSize": min(limit, 50)},
    )
    transfers = [t for t in (res.get("transfers") or []) if isinstance(t, dict)]
    return [
        {
            "chain": t.get("blockchain", ""),
            "from": t.get("from", ""),
            "to": t.get("to", ""),
            "value_usd": round(usd(t.get("usdValue")), 2),
            "timestamp": t.get("timestamp"),
            "token": t.get("tokenSymbol", ""),
        }
        for t in transfers[:limit]
    ]
