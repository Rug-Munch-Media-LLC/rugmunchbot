"""chart pair resolution — DexScreener search, real pairs only.

Extracted verbatim from the former single-module ``tools/charting.py``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import DEXSCREENER
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.charting._constants import _CHAIN_ALIASES


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_chain(chain: str) -> str:
    key = (chain or "").strip().lower()
    return _CHAIN_ALIASES.get(key, key)


# ── Pair resolution (DexScreener; real pairs only) ─────────────────────────


def _normalize_pairs(data: Any) -> list[dict[str, Any]]:
    """Normalize DexScreener pair payloads (dict {"pairs": [...]} or list)."""
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
    elif isinstance(data, list):
        pairs = data
    else:
        pairs = []
    return [p for p in pairs if isinstance(p, dict)]


def _is_pair(p: dict[str, Any]) -> bool:
    return bool(p.get("pairAddress")) and isinstance(p.get("baseToken"), dict)


def _pick_best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest-24h-volume pair (real volume from the source)."""
    best: dict[str, Any] | None = None
    best_vol = -1.0
    for p in pairs:
        vol = _to_float((p.get("volume") or {}).get("h24")) or 0.0
        if vol > best_vol:
            best_vol = vol
            best = p
    return best


async def _resolve_pair(address: str, symbol: str, chain: str) -> dict[str, Any] | None:
    """Resolve a DexScreener pair for an address or ``$SYMBOL``.

    Address queries use the exact token endpoint; symbol queries use search
    and prefer a pair whose baseToken symbol matches exactly. ``chain`` (when
    given) filters pairs; the winner is the highest-24h-volume pair.
    """
    if address:
        body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
        pairs = [p for p in _normalize_pairs(body) if _is_pair(p)]
        if chain:
            want = _normalize_chain(chain)
            matched = [p for p in pairs if _normalize_chain(str(p.get("chainId") or "")) == want]
            if matched:
                pairs = matched
        return _pick_best_pair(pairs)

    q = (symbol or "").strip().upper()
    if not q:
        return None
    body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
    pairs = [p for p in _normalize_pairs(body) if _is_pair(p)]
    if not pairs:
        return None
    exact = [p for p in pairs if ((p.get("baseToken") or {}).get("symbol") or "").upper() == q]
    if chain:
        want = _normalize_chain(chain)
        exact = [p for p in exact if _normalize_chain(str(p.get("chainId") or "")) == want] or exact
    return _pick_best_pair(exact or pairs)


def _pair_snapshot(pair: dict[str, Any], address: str, symbol: str) -> dict[str, Any]:
    """Real DexScreener pair metrics (never synthesized)."""
    base = pair.get("baseToken") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}
    liquidity_raw = pair.get("liquidity")
    liquidity = liquidity_raw if isinstance(liquidity_raw, dict) else {}
    return {
        "address": address or base.get("address") or "",
        "symbol": (base.get("symbol") or "").strip().upper() or (symbol or "").strip().upper(),
        "name": (base.get("name") or "").strip(),
        "chain": _normalize_chain(str(pair.get("chainId") or "")),
        "dex": pair.get("dexId") or "",
        "pair_address": pair.get("pairAddress") or "",
        "price_usd": _to_float(pair.get("priceUsd")),
        "change_1h": _to_float(price_change.get("h1")),
        "change_6h": _to_float(price_change.get("h6")),
        "change_24h": _to_float(price_change.get("h24")),
        "volume_24h": _to_float(volume.get("h24")),
        "liquidity_usd": _to_float(liquidity.get("usd")),
    }
