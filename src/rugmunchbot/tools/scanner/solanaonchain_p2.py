"""_solana_onchain.py split part: _birdeye_holder_count, resolve_holder_stats, resolve_deployer, _buyers_from_tx."""
from __future__ import annotations
from typing import Any
import asyncio
import os
from .solanaonchain_p1 import _rpc, _rpc_rotating, logger  # noqa: F401

async def _birdeye_holder_count(mint: str) -> int | None:
    """Total holder count from Birdeye ``token_overview`` (key-gated).

    Free RPC can rank the largest accounts but cannot cheaply COUNT holders;
    Birdeye reports the exact number. Returns ``None`` when no
    ``BIRDEYE_API_KEY`` is configured or the lookup fails — never raises.
    """
    key = os.getenv("BIRDEYE_API_KEY", "").strip()
    if not key:
        return None
    try:
        from rugmunchbot.tools._client import http_get_json

        body = await http_get_json(
            "https://public-api.birdeye.so/defi/token_overview",
            params={"address": mint},
            headers={"X-API-KEY": key, "x-chain": "solana"},
            timeout=6.0,
        )
        data = body.get("data") if isinstance(body, dict) else None
        raw = data.get("holder_count") if isinstance(data, dict) else None
        return int(float(raw)) if raw is not None else None
    except Exception:
        logger.debug("birdeye_holder_count_failed for %s", mint)
        return None

async def resolve_holder_stats(mint: str) -> dict[str, Any] | None:
    """Top-holder concentration straight from RPC (always available).

    ``getTokenLargestAccounts`` returns the 20 largest token accounts;
    combined with ``getTokenSupply`` this yields exact top-10/top-20 supply
    concentration plus the ranked holder rows. Total holder COUNT comes from
    Birdeye when ``BIRDEYE_API_KEY`` is set — this function never invents a
    count it did not receive.
    """
    largest, supply = await asyncio.gather(
        _rpc_rotating("getTokenLargestAccounts", [mint]),
        _rpc("getTokenSupply", [mint], timeout=6.0),
    )
    accounts = (
        ((largest or {}).get("result", {}).get("value") or []) if isinstance(largest, dict) else []
    )
    # Rate-limited providers can return a SUCCESSFUL response with an empty
    # account list (not an error — so endpoint rotation never triggers).
    # Retry once after a short backoff before giving up.
    if not accounts:
        await asyncio.sleep(1.5)
        largest = await _rpc_rotating("getTokenLargestAccounts", [mint])
        accounts = (
            ((largest or {}).get("result", {}).get("value") or [])
            if isinstance(largest, dict)
            else []
        )
    supply_ui = (
        float(((supply or {}).get("result", {}).get("value") or {}).get("uiAmount") or 0)
        if isinstance(supply, dict)
        else 0.0
    )
    if not accounts or supply_ui <= 0:
        return None

    holders: list[dict[str, Any]] = []
    for acct in accounts[:20]:
        addr = acct.get("address") or ""
        ui = acct.get("uiAmount")
        try:
            amount = float(ui or 0)
        except (TypeError, ValueError):
            continue
        if not addr or amount <= 0:
            continue
        holders.append({"address": addr, "pct": round(amount / supply_ui * 100, 2)})
    if not holders:
        return None
    top10 = round(sum(h["pct"] for h in holders[:10]), 2)
    top20 = round(sum(h["pct"] for h in holders[:20]), 2)
    stats: dict[str, Any] = {
        "top_holders": holders,
        "top_10_pct": top10,
        "top_20_pct": top20,
        "source": "rpc:getTokenLargestAccounts",
    }
    holder_count = await _birdeye_holder_count(mint)
    if holder_count is not None:
        stats["holder_count"] = holder_count
    return stats

async def resolve_deployer(mint: str) -> str | None:
    """Deployer identity from the mint's earliest reachable transaction.

    Walks ``getSignaturesForAddress`` back to the oldest signature in the
    window and reads the creation transaction's fee payer — the wallet that
    paid to create the mint (for pump.fun-style launches this is the human
    creator even when mint authority sits with the factory program).
    """
    sigs_resp = await _rpc("getSignaturesForAddress", [mint, {"limit": 1000}], timeout=8.0)
    sigs = (sigs_resp or {}).get("result") if isinstance(sigs_resp, dict) else None
    if not isinstance(sigs, list) or not sigs:
        return None
    oldest = sigs[-1]
    signature = oldest.get("signature") if isinstance(oldest, dict) else None
    if not signature:
        return None
    tx_resp = await _rpc(
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        timeout=8.0,
    )
    tx = (tx_resp or {}).get("result") if isinstance(tx_resp, dict) else None
    if not isinstance(tx, dict):
        return None
    message = tx.get("transaction", {}).get("message", {})
    keys = message.get("accountKeys") or []
    # jsonParsed rows carry {"pubkey","signer","writable"}; raw rows are strings.
    for key in keys:
        if isinstance(key, dict) and key.get("signer"):
            return key.get("pubkey") or None
    if keys and isinstance(keys[0], str):
        return keys[0]
    header = message.get("header", {})
    if isinstance(header, dict) and (header.get("numRequiredSignatures") or 0) >= 1:
        static_keys = message.get("accountKeys") or []
        if static_keys and isinstance(static_keys[0], dict):
            return static_keys[0].get("pubkey")
    return None

def _buyers_from_tx(tx: dict[str, Any], mint: str) -> list[str]:
    """Wallets whose token balance for ``mint`` increased in this tx."""
    buyers: set[str] = set()
    meta = tx.get("meta") or {}
    pre_by_index: dict[Any, float] = {}
    for bal in meta.get("preTokenBalances") or []:
        if not isinstance(bal, dict) or (bal.get("mint") or "") != mint:
            continue
        try:
            pre_by_index[bal.get("accountIndex")] = float(
                (bal.get("uiTokenAmount") or {}).get("uiAmount") or 0
            )
        except (TypeError, ValueError):
            continue
    for bal in meta.get("postTokenBalances") or []:
        if not isinstance(bal, dict) or (bal.get("mint") or "") != mint:
            continue
        try:
            after = float((bal.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        except (TypeError, ValueError):
            continue
        if after > pre_by_index.get(bal.get("accountIndex"), 0.0):
            owner = bal.get("owner") or ""
            if owner:
                buyers.add(owner)
    return sorted(buyers)
