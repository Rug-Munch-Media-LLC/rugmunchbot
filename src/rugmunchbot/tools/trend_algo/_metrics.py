"""trend_algo per-token metrics — DexScreener search + optional Birdeye.

Extracted verbatim from the former single-module ``tools/trend_algo.py``.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.config import DEXSCREENER
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.trend_algo._normalize import _normalize_chain, _to_float, _to_int


def _time_ms_now() -> float:
    import time

    return time.time() * 1000.0


async def _fetch_pair_metrics(address: str) -> dict[str, Any] | None:
    """Real per-token metrics from DexScreener. None when unresolvable.

    Returns the HIGHEST-24h-volume pair whose baseToken matches ``address``.
    ``symbol`` is the real DexScreener baseToken symbol — never synthesised.
    """
    body = await http_get_json(f"{DEXSCREENER}/search", params={"q": address}, timeout=8.0)
    if not isinstance(body, dict):
        return None
    pairs = body.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return None
    best: dict[str, Any] | None = None
    best_vol = -1.0
    for p in pairs:
        if not isinstance(p, dict):
            continue
        bt = p.get("baseToken")
        if not isinstance(bt, dict) or bt.get("address") != address:
            continue
        symbol = (bt.get("symbol") or "").strip()
        if not symbol:
            continue
        vol = (
            _to_float(
                (p.get("volume") or {}).get("h24") if isinstance(p.get("volume"), dict) else None
            )
            or 0.0
        )
        if vol > best_vol:
            best_vol = vol
            best = p
    if best is None:
        return None
    bt = best.get("baseToken") or {}
    volume = best.get("volume") or {}
    price_change = best.get("priceChange") or {}
    liquidity = best.get("liquidity") or {}
    txns = best.get("txns") or {}
    h24_txns = txns.get("h24") if isinstance(txns, dict) else None
    created_ms = _to_float(best.get("pairCreatedAt"))
    age_hours = max((_time_ms_now() - created_ms) / 3_600_000.0, 0.0) if created_ms else None
    return {
        "address": address,
        "chain": _normalize_chain(str(best.get("chainId") or "")),
        "symbol": (bt.get("symbol") or "").strip().upper(),
        "name": (bt.get("name") or "").strip(),
        "dex_id": str(best.get("dexId") or ""),
        "price_usd": _to_float(best.get("priceUsd")),
        "volume_24h": _to_float(volume.get("h24")),
        "liquidity_usd": _to_float(liquidity.get("usd")),
        "price_change_24h": _to_float(price_change.get("h24")),
        "buys_24h": _to_int(h24_txns.get("buys") if isinstance(h24_txns, dict) else None),
        "sells_24h": _to_int(h24_txns.get("sells") if isinstance(h24_txns, dict) else None),
        "age_hours": age_hours,
        "url": str(best.get("url") or ""),
    }


async def _birdeye_holders(address: str, chain: str) -> int | None:
    """Optional holder count from Birdeye. Never blocks on failure."""
    key = os.getenv("BIRDEYE_API_KEY", "").strip()
    if not key:
        return None
    try:
        body = await http_get_json(
            "https://public-api.birdeye.so/defi/token_overview",
            params={"address": address},
            headers={"X-API-KEY": key, "x-chain": chain},
            timeout=5.0,
        )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return None
        return _to_int(data.get("holder"))
    except Exception:  # pragma: no cover — defensive, never blocks
        return None
