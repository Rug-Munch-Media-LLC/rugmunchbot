"""Data sanity validation for The Daily Rug (2026-08-23).

Doctrine: NEVER publish a number that fails plausibility. Worst observed
failure: a fresh GMGN meme pair symbol-colliding with a major ("SOL") and its
$20M mcap printed as Solana's. Every market fact passes through here.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Plausibility floors (USD) for majors — a "SOL" claiming less is an impostor.
MAJOR_MCAP_FLOORS: dict[str, float] = {
    "BTC": 400e9, "ETH": 100e9, "SOL": 15e9, "BNB": 20e9, "XRP": 15e9,
    "ADA": 5e9, "DOGE": 8e9, "AVAX": 2e9, "LINK": 3e9, "TON": 2e9,
}

# Price sanity bands (USD) — catch stale/corrupt spot prints.
PRICE_BANDS: dict[str, tuple[float, float]] = {
    "BTC": (10_000, 500_000), "ETH": (500, 25_000), "SOL": (20, 2_000),
    "XRP": (0.20, 50), "BNB": (100, 5_000),
}


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def check_mover(item: dict[str, Any]) -> str | None:
    """Return a rejection reason for a mover row, or None if plausible."""
    sym = str(item.get("symbol") or item.get("ticker") or "").upper().replace("USDT", "")
    mcap = _num(item.get("market_cap") or item.get("mcap"))
    vol = _num(item.get("total_volume") or item.get("volume"))
    name = str(item.get("name") or "")

    floor = MAJOR_MCAP_FLOORS.get(sym)
    if floor is not None:
        if mcap is None:
            return f"symbol_collision_risk: '{sym}' lacks mcap (cannot verify floor)"
        if mcap < floor:
            return (
                f"symbol_collision: '{sym}' mcap ${mcap:,.0f} < {sym} floor "
                f"${floor:,.0f} (impostor pair, name={name!r})"
            )
        if vol is not None and mcap > 0 and vol / mcap > 1.5:
            return f"volume_absurd: '{sym}' vol/mcap={vol / mcap:.2f}"

    if vol is not None and mcap is not None and mcap > 0 and vol > mcap * 20:
        return f"volume_absurd: vol ${vol:,.0f} >> mcap ${mcap:,.0f}"
    return None


def check_price(sym: str, price: Any) -> str | None:
    band = PRICE_BANDS.get(sym.upper())
    p = _num(price)
    if band and p is not None and not (band[0] <= p <= band[1]):
        return f"price_out_of_band: {sym} ${p:,.2f} outside [{band[0]:.0f}, {band[1]:.0f}]"
    return None


def sanitize_movers(movers: dict[str, Any]) -> dict[str, Any]:
    """Filter gainers/losers through plausibility; log every rejection."""
    out: dict[str, Any] = {}
    for side in ("gainers", "losers"):
        kept, rejected = [], []
        for item in movers.get(side) or []:
            reason = check_mover(item)
            if reason:
                rejected.append(reason)
                logger.warning("daily_rug:rejected_mover side=%s %s", side, reason)
            else:
                kept.append(item)
        out[side] = kept
        out[f"{side}_rejected"] = rejected
    return out


def sanitize_prices(prices: dict[str, Any]) -> dict[str, Any]:
    """Drop any major price print that fails its band; log rejections."""
    clean: dict[str, Any] = {}
    for sym, val in (prices or {}).items():
        if isinstance(val, dict):
            bad = check_price(sym, val.get("price"))
            if bad:
                logger.warning("daily_rug:rejected_price %s", bad)
                continue
        clean[sym] = val
    return clean
