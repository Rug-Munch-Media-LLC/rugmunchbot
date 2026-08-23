"""Ankr Advanced API client — indexed multichain holder/token intelligence.

Freemium: 200M credits/mo, 50 reqs/min. Complements RPC scanning with
pre-indexed data (getTokenHolders, getAccountBalance, getTransfersOfAsset)
on chains where our own indexers are thin. Real data only; fail loud.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MULTICHAIN = "https://rpc.ankr.com/multichain"


def _key() -> str:
    return os.getenv("ANKR_API_KEY", "").strip()


async def _call(method: str, params: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    key = _key()
    if not key:
        raise RuntimeError("ANKR_API_KEY unset — Advanced API unavailable")
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(
            MULTICHAIN,
            json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            headers={"X-API-KEY": key},
        )
        if r.status_code == 429:
            raise RuntimeError("ankr rate-limited (50 req/min freemium)")
        if r.status_code != 200:
            raise RuntimeError(f"ankr HTTP {r.status_code}")
        d = r.json()
        if "error" in d:
            raise RuntimeError(f"ankr error: {d['error']}")
        return d.get("result", {})


async def token_holders(
    contract_address: str, chain: str = "eth", limit: int = 50
) -> list[dict[str, Any]]:
    """Indexed holder list for a token (no scan needed)."""
    res = await _call(
        "ankr_getTokenHolders",
        {"blockchain": chain, "contractAddress": contract_address, "pageSize": min(limit, 100)},
    )
    return (res.get("holders") or [])


async def account_balance(wallet: str) -> dict[str, Any]:
    """Multichain balance summary for a wallet."""
    return await _call("ankr_getAccountBalance", {"walletAddress": wallet})


async def token_transfers(
    contract_address: str, chain: str = "eth", limit: int = 100, from_block: int | None = None
) -> list[dict[str, Any]]:
    """Recent transfers of a token — wash-pattern raw material."""
    params: dict[str, Any] = {
        "blockchain": chain,
        "contractAddress": contract_address,
        "pageSize": min(limit, 100),
    }
    if from_block:
        params["fromBlock"] = from_block
    res = await _call("ankr_getTransfersOfAsset", params)
    return res.get("transfers") or []


# ── Moralis comparison note (2026-08-23 live probing) ──────────────────────
# Moralis freemium proved EXTREMELY tight: basic wallet balance works
# sparingly, then hard-403s. Rich endpoints (wallet history, swaps, PnL,
# DeFi positions, erc20 transfers/metadata, net-worth) are PAID-ONLY (403).
# Verdict: Ankr Advanced (this module, 200M credits/mo) is our indexed-data
# primary. Moralis key kept in gopass for occasional single balance checks:
#
async def moralis_wallet_balance(wallet: str, chain: str = "eth") -> int | None:
    """Occasional-use Moralis native balance (respects their tight free tier)."""
    import os as _os

    key = _os.getenv("MORALIS_API_KEY", "").strip()
    if not key:
        return None
    import httpx as _httpx

    async with _httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            f"https://deep-index.moralis.io/api/v2.2/{wallet}/balance",
            params={"chain": chain},
            headers={"X-API-Key": key},
        )
        if r.status_code == 200:
            return int(r.json().get("balance", 0))
        return None
