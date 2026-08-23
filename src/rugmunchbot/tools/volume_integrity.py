"""Volume integrity analysis — wash-trading & fake-volume detection.

The Daily Rug doctrine applied to tokens: never trust a number that fails
plausibility. Heuristics over DexScreener pair data:

  1. VOL/MCAP  — >150% daily turnover on a small cap is bot-churn territory.
  2. VOL/LIQ   — volume >> liquidity means prints are not real two-way flow.
  3. BUY RATIO — sustained >85% buys with high volume = self-dealing bots.
  4. TX SIZE   — avg trade size vs liquidity: dust-storms (thousands of tiny
                 buys) are the classic wash pattern.
  5. PRICE FLAT + VOLUME SPIKE — price unmoved while "volume" pours = wash.

Composite score 0-100 (100 = clean), verdict + per-check flags. Fail-open:
data gaps degrade the score honestly instead of lying.
"""

from __future__ import annotations

import logging
from typing import Any

from rugmunchbot.tools.registry import ToolResult

logger = logging.getLogger(__name__)


def _f(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


async def _get_pairs(address: str, chain: str) -> list[dict[str, Any]]:
    """DexScreener pairs via the vendored unified_scanner data source."""
    try:
        from rugmunchbot._vendor.unified_scanner._data_sources import (
            get_dexscreener_pairs,
        )

        pairs = await get_dexscreener_pairs(address=address, chain=chain)
        return [p for p in (pairs or []) if isinstance(p, dict)]
    except Exception as exc:
        logger.warning("volume_integrity:pairs_failed err=%s", str(exc)[:120])
        return []


def _pick_primary(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest-liquidity pair = primary market."""
    if not pairs:
        return None
    return max(
        pairs,
        key=lambda p: _f((p.get("liquidity") or {}).get("usd")) or 0.0,
    )


def analyze(pair: dict[str, Any]) -> dict[str, Any]:
    """Run the five heuristics against one DexScreener pair payload."""
    checks: list[dict[str, str]] = []
    score = 100.0

    vol24 = _f((pair.get("volume") or {}).get("h24"))
    liq = _f((pair.get("liquidity") or {}).get("usd"))
    mcap = _f(pair.get("marketCap")) or _f(pair.get("fdv"))
    txns = (pair.get("txns") or {}).get("h24") or {}
    buys = _f(txns.get("buys")) or 0.0
    sells = _f(txns.get("sells")) or 0.0
    total_tx = buys + sells
    price_change = _f((pair.get("priceChange") or {}).get("h24"))

    # 1) VOL/MCAP
    if vol24 is not None and mcap and mcap > 0:
        ratio = vol24 / mcap
        if ratio > 1.5:
            score -= 30
            checks.append({"check": "vol_mcap", "value": f"{ratio:.0%}", "flag": "CRITICAL", "note": ">150% daily turnover — bot churn pattern"})
        elif ratio > 0.8:
            score -= 12
            checks.append({"check": "vol_mcap", "value": f"{ratio:.0%}", "flag": "WARN", "note": "unusually hot turnover"})
        else:
            checks.append({"check": "vol_mcap", "value": f"{ratio:.0%}", "flag": "OK", "note": "plausible turnover"})

    # 2) VOL/LIQ
    if vol24 is not None and liq and liq > 0:
        ratio = vol24 / liq
        if ratio > 25:
            score -= 30
            checks.append({"check": "vol_liq", "value": f"{ratio:.1f}x", "flag": "CRITICAL", "note": "volume cannot exit through this liquidity — fake prints"})
        elif ratio > 8:
            score -= 12
            checks.append({"check": "vol_liq", "value": f"{ratio:.1f}x", "flag": "WARN", "note": "thin liquidity vs claimed volume"})
        else:
            checks.append({"check": "vol_liq", "value": f"{ratio:.1f}x", "flag": "OK", "note": "flow fits liquidity"})

    # 3) BUY RATIO
    if total_tx >= 50 and vol24 and vol24 > 10_000:
        buy_ratio = buys / total_tx
        if buy_ratio > 0.85:
            score -= 25
            checks.append({"check": "buy_ratio", "value": f"{buy_ratio:.0%} buys", "flag": "CRITICAL", "note": "near-one-way buying at scale = self-dealing"})
        elif buy_ratio > 0.72:
            score -= 8
            checks.append({"check": "buy_ratio", "value": f"{buy_ratio:.0%} buys", "flag": "WARN", "note": "one-sided flow"})
        else:
            checks.append({"check": "buy_ratio", "value": f"{buy_ratio:.0%} buys", "flag": "OK", "note": "two-way market"})

    # 4) AVG TRADE SIZE vs LIQUIDITY (dust storm)
    if total_tx > 0 and vol24 is not None and liq and liq > 0:
        avg_trade = vol24 / total_tx
        dust_ratio = avg_trade / liq
        if total_tx > 300 and dust_ratio < 0.0005:
            score -= 20
            checks.append({"check": "tx_size", "value": f"${avg_trade:,.0f} avg", "flag": "WARN", "note": f"dust storm: {int(total_tx)} micro-trades"})
        else:
            checks.append({"check": "tx_size", "value": f"${avg_trade:,.0f} avg", "flag": "OK", "note": "normal ticket size"})

    # 5) FLAT PRICE + VOLUME SPIKE
    if price_change is not None and vol24 is not None and mcap and mcap < 50e6:
        if abs(price_change) < 1.0 and vol24 / mcap > 0.5:
            score -= 22
            checks.append({"check": "price_vol_divergence", "value": f"{price_change:+.1f}% @ {vol24/mcap:.0%} vol", "flag": "CRITICAL", "note": "massive volume, zero price discovery = wash"})
        else:
            checks.append({"check": "price_vol_divergence", "value": f"{price_change:+.1f}%", "flag": "OK", "note": "price responds to flow"})

    score = max(0.0, min(100.0, score))
    if score >= 80:
        verdict = "CLEAN"
    elif score >= 55:
        verdict = "SUSPECT"
    else:
        verdict = "LIKELY_WASHED"
    return {"score": round(score, 1), "verdict": verdict, "checks": checks}


async def volume_integrity(*, address: str = "", chain: str = "") -> ToolResult:
    """ToolResult: composite volume-integrity report for a token address."""
    if not address:
        return ToolResult(tool="volume_integrity", ok=False, data={"error": "address required"})
    pairs = await _get_pairs(address=address, chain=chain)
    if not pairs:
        return ToolResult(tool="volume_integrity", ok=False, data={"error": "no dexscreener pairs found"})
    primary = _pick_primary(pairs)
    report = analyze(primary)
    report.update({
        "address": address,
        "chain": chain or primary.get("chainId"),
        "pair": f"{(primary.get('baseToken') or {}).get('symbol')}/{(primary.get('quoteToken') or {}).get('symbol')}",
        "liquidity_usd": _f((primary.get("liquidity") or {}).get("usd")),
        "volume24_usd": _f((primary.get("volume") or {}).get("h24")),
        "pairs_analyzed": len(pairs),
    })
    return ToolResult(tool="volume_integrity", ok=True, data=report)
