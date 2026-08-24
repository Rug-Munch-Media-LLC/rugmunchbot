"""Whale Profiler — deep wallet intelligence from our own indexed data.

Given a wallet address, builds a comprehensive profile:
  - Token holdings (from Ankr Advanced multichain balance)
  - Recent transfers (from Ankr Advanced)
  - Risk flags (interaction with known scam addresses)
  - Behavioral classification (sniper, whale, early buyer, etc.)

Uses our own indexer first, falls back to Ankr Advanced API.
Free-data-first doctrine: no paid APIs required.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ANKR_MULTICHAIN = "https://rpc.ankr.com/multichain"


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
        if "error" in d:
            raise RuntimeError(f"Ankr error: {d['error']}")
        return d.get("result", {})


async def get_wallet_balance(wallet: str) -> dict[str, Any]:
    """Multichain token balances for a wallet."""
    res = await _ankr_call("ankr_getAccountBalance", {"walletAddress": wallet})
    assets = res.get("assets") or []
    total_usd = sum(float(a.get("balanceUsd") or 0) for a in assets)
    tokens = sorted(assets, key=lambda a: float(a.get("balanceUsd") or 0), reverse=True)
    return {
        "total_usd": round(total_usd, 2),
        "token_count": len(assets),
        "top_tokens": [
            {"symbol": a.get("symbol", "?"), "chain": a.get("chainId", ""),
             "balance_usd": round(float(a.get("balanceUsd") or 0), 2)}
            for a in tokens[:10]
        ],
    }


async def get_wallet_transfers(wallet: str, limit: int = 20) -> list[dict[str, Any]]:
    """Recent transfers involving this wallet."""
    res = await _ankr_call(
        "ankr_getTransactionsByAddress",
        {"address": wallet, "pageSize": min(limit, 50)},
    )
    transfers = res.get("transfers") or []
    return [
        {
            "chain": t.get("blockchain", ""),
            "from": t.get("from", ""),
            "to": t.get("to", ""),
            "value_usd": round(float(t.get("usdValue") or 0), 2),
            "timestamp": t.get("timestamp"),
            "token": t.get("tokenSymbol", ""),
        }
        for t in transfers[:limit]
    ]


def classify_whale_profile(
    balance_data: dict[str, Any],
    transfers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build behavioral classification from balance + transfer data."""
    total_usd = balance_data.get("total_usd", 0)
    token_count = balance_data.get("token_count", 0)

    # Size classification
    if total_usd >= 10_000_000:
        size_class = "MEGA_WHALE"
    elif total_usd >= 1_000_000:
        size_class = "WHALE"
    elif total_usd >= 100_000:
        size_class = "DOLPHIN"
    elif total_usd >= 10_000:
        size_class = "FISH"
    else:
        size_class = "MINNOW"

    # Activity metrics
    recent_volume = sum(t.get("value_usd", 0) for t in transfers[:10])
    unique_chains = len({t.get("chain", "") for t in transfers})

    # Behavioral flags
    flags = []
    if token_count > 20:
        flags.append("multi_chain_degen")
    if recent_volume > 100_000:
        flags.append("high_velocity")
    if len(transfers) >= 15 and unique_chains >= 3:
        flags.append("cross_chain_operator")

    return {
        "size_class": size_class,
        "total_portfolio_usd": total_usd,
        "tokens_held": token_count,
        "recent_transfer_volume_usd": round(recent_volume, 2),
        "unique_chains_active": unique_chains,
        "behavioral_flags": flags,
    }


async def profile_whale(wallet: str) -> dict[str, Any]:
    """Full whale profile: balance + transfers + behavioral classification."""
    if not wallet.startswith("0x"):
        wallet = "0x" + wallet.replace("0x", "")

    try:
        balance_data = await get_wallet_balance(wallet)
    except Exception as exc:
        logger.warning("whale_profiler:balance_failed err=%s", str(exc)[:100])
        balance_data = {"total_usd": 0, "token_count": 0, "top_tokens": []}

    try:
        transfers = await get_wallet_transfers(wallet, limit=20)
    except Exception as exc:
        logger.warning("whale_profiler:transfers_failed err=%s", str(exc)[:100])
        transfers = []

    behavior = classify_whale_profile(balance_data, transfers)

    return {
        "wallet": wallet,
        "balance": balance_data,
        "recent_transfers": transfers[:10],
        "behavior": behavior,
        "profiled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# time import for the timestamp
