"""Scanner engine — DexScreener best-pair market snapshot helpers.

DexScreener returns many pairs per token and ``pairs[0]`` is often a
pump.fun bonding-curve pair with ``liquidity.usd: 0``; these helpers pick
the pair with the highest non-zero liquidity (ties broken by 24h volume)
and merge the honest snapshot into a legacy scan result.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.tools.scanner._providers import _safe_float

#: DexScreener timeframe keys we actually surface (priceChange m1/m5/h1/h6/h24,
#: volume m5/h1/h6/h24). DexScreener exposes no 7d window — those stay honest
#: "—" unless a provider reports them.
_DS_PRICE_CHANGE_TFS = ("m1", "m5", "h1", "h6", "h24")
_DS_VOLUME_TFS = ("m5", "h1", "h6", "h24")


def _ds_timeframe_map(raw: Any, allowed: tuple[str, ...]) -> dict[str, float | None]:
    """Extract a per-timeframe numeric map from a DexScreener sub-dict.

    Returns only the timeframes DexScreener actually provides (``allowed``);
    each value is a float when the payload carried a number, else None — a
    missing timeframe is NEVER coerced to 0.0 (keep-it-honest rule).
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float | None] = {}
    for tf in allowed:
        v = raw.get(tf)
        try:
            out[tf] = float(v) if v is not None else None
        except (TypeError, ValueError):
            out[tf] = None
    return out


def _ds_market_snapshot(pairs: Any) -> dict[str, Any]:
    """Best-pair market snapshot from a DexScreener ``pairs`` list.

    Picks the pair with the highest non-zero liquidity (ties broken by 24h
    volume) — DexScreener returns many pairs per token and ``pairs[0]`` is
    often a pump.fun bonding-curve pair with ``liquidity.usd: 0``, which is
    why the report showed ``Liquidity: $0`` for normal tokens. Returns the
    snapshot with honest ``None`` for fields the pair data lacks::

        {
            "liquidity": float | None,
            "volume_24h": float | None,
            "market_cap": float | None,
            "price": float | None,
            "pair_address": str,
            "dex": str,
            "created_at": int | None,
            "volume_tf": {"h1": float|None, "h6": ..., "h24": ...},
            "price_change_tf": {"h1": ..., "h6": ..., "h24": ...},
        }

    Returns an empty dict when there are no usable pairs (callers treat an
    empty dict as "no market data" and keep their existing fallbacks).
    """
    if not isinstance(pairs, list):
        return {}
    best: dict[str, Any] | None = None
    best_score = (-1.0, 0.0)
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        liq = _safe_float((pair.get("liquidity") or {}).get("usd"))
        vol = _safe_float((pair.get("volume") or {}).get("h24"))
        score = (liq, vol)
        if score > best_score:
            best_score = score
            best = pair
    if best is None:
        return {}
    base = best.get("baseToken") or {}
    return {
        "liquidity": best_score[0] if best_score[0] > 0 else None,
        "volume_24h": _safe_float((best.get("volume") or {}).get("h24")) or None,
        "market_cap": _safe_float(best.get("marketCap") or best.get("fdv")) or None,
        "price": _safe_float(best.get("priceUsd")) or None,
        "pair_address": best.get("pairAddress", "") or "",
        "dex": best.get("dexId", "") or "",
        "created_at": best.get("pairCreatedAt"),
        "symbol": (base.get("symbol") or "") if isinstance(base, dict) else "",
        "volume_tf": _ds_timeframe_map(best.get("volume"), _DS_VOLUME_TFS),
        "price_change_tf": _ds_timeframe_map(best.get("priceChange"), _DS_PRICE_CHANGE_TFS),
    }


def _merge_market_snapshot(result: dict[str, Any], snap: dict[str, Any]) -> None:
    """Merge a DexScreener market snapshot into a legacy scan result.

    Fills the report's Market Data keys from real pair data, never
    overwriting values the primary scan already provided (the v2 backend /
    oracle win; DexScreener only tops up gaps). Timeframe volume/price-change
    are stored under ``volume_1h/6h/24h`` + ``price_change_1h/6h/24h`` so the
    formatter can render multi-timeframe figures. Missing fields stay None —
    the formatter renders "—", never a fabricated 0.
    """
    if not isinstance(snap, dict) or not snap:
        return
    if not result.get("liquidity") and snap.get("liquidity"):
        result["liquidity"] = snap["liquidity"]
    if not result.get("volume_24h") and snap.get("volume_24h"):
        result["volume_24h"] = snap["volume_24h"]
    if not result.get("mcap") and snap.get("market_cap"):
        result["mcap"] = snap["market_cap"]
    if not result.get("price") and snap.get("price"):
        result["price"] = snap["price"]
    # DexScreener timeframe keys are h1/h6/h24; the report reads 1h/6h/24h.
    tf_to_report = {"h1": "1h", "h6": "6h", "h24": "24h"}
    for tf, report_tf in tf_to_report.items():
        vol = (snap.get("volume_tf") or {}).get(tf)
        if vol is not None:
            result[f"volume_{report_tf}"] = vol
        pc = (snap.get("price_change_tf") or {}).get(tf)
        if pc is not None:
            result[f"price_change_{report_tf}"] = pc
    # m1/m5 price change is too noisy for the Market Data block — keep the
    # mapping complete under ``price_change_min`` for any consumer that wants
    # the finer windows.
    for tf in ("m1", "m5"):
        pc = (snap.get("price_change_tf") or {}).get(tf)
        if pc is not None:
            result.setdefault("price_change_min", {})[tf] = pc
    if not result.get("pair_address") and snap.get("pair_address"):
        result["pair_address"] = snap["pair_address"]
    if not result.get("dex") and snap.get("dex"):
        result["dex"] = snap["dex"]
