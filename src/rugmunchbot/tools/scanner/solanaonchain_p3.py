"""_solana_onchain.py split part: detect_same_slot_bundle."""
from __future__ import annotations
from typing import Any
import asyncio
from .solanaonchain_p1 import _BUNDLE_MAX_TXS, _BUNDLE_MIN_WALLETS_PER_SLOT, _BUNDLE_TX_CONCURRENCY, _rpc  # noqa: F401
from .solanaonchain_p2 import _buyers_from_tx  # noqa: F401

async def detect_same_slot_bundle(mint: str) -> dict[str, Any] | None:
    """Same-slot coordinated-buy detection (bot-native, RPC-only).

    Fetches the launch-window transactions, groups buyers by slot, and
    reports slots where ≥ ``_BUNDLE_MIN_WALLETS_PER_SLOT`` distinct wallets
    bought atomically — the fingerprint of sniper/bundler bots. Mirrors the
    backend engine's methodology but runs entirely on public RPC so it works
    even when the backend is unreachable.
    """
    sigs_resp = await _rpc(
        "getSignaturesForAddress", [mint, {"limit": _BUNDLE_MAX_TXS}], timeout=8.0
    )
    sigs = (sigs_resp or {}).get("result") if isinstance(sigs_resp, dict) else None
    if not isinstance(sigs, list) or not sigs:
        return None

    sem = asyncio.Semaphore(_BUNDLE_TX_CONCURRENCY)

    async def _fetch(signature: str) -> tuple[int, list[str]] | None:
        async with sem:
            tx_resp = await _rpc(
                "getTransaction",
                [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
                timeout=8.0,
            )
        tx = (tx_resp or {}).get("result") if isinstance(tx_resp, dict) else None
        if not isinstance(tx, dict):
            return None
        slot = tx.get("slot")
        if not isinstance(slot, int):
            return None
        return slot, _buyers_from_tx(tx, mint)

    signatures = [
        s["signature"]
        for s in sigs[:_BUNDLE_MAX_TXS]
        if isinstance(s, dict) and isinstance(s.get("signature"), str)
    ]
    results = await asyncio.gather(
        *[_fetch(sig) for sig in signatures],
        return_exceptions=True,
    )

    by_slot: dict[int, set[str]] = {}
    for res in results:
        if isinstance(res, BaseException) or res is None:
            continue
        slot, buyers = res
        if buyers:
            by_slot.setdefault(slot, set()).update(buyers)

    clusters: list[dict[str, Any]] = [
        {
            "slot": slot,
            "wallets": sorted(wallets),
            "buyer_count": len(wallets),
        }
        for slot, wallets in by_slot.items()
        if len(wallets) >= _BUNDLE_MIN_WALLETS_PER_SLOT
    ]
    if not clusters:
        return {
            "is_bundled": False,
            "method": "same_slot_rpc",
            "slots_scanned": len(by_slot),
            "clusters": [],
        }
    clusters.sort(key=lambda c: int(c["buyer_count"]), reverse=True)
    top = clusters[0]
    all_bundle_wallets: set[str] = set()
    for c in clusters:
        all_bundle_wallets.update(c["wallets"])
    return {
        "is_bundled": True,
        "method": "same_slot_rpc",
        "slots_scanned": len(by_slot),
        "max_wallets_one_slot": top["buyer_count"],
        "total_bundle_wallets": len(all_bundle_wallets),
        "clusters": clusters[:5],
    }
