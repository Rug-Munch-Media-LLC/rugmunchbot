"""Daily perps desk recap — funding, open interest, liquidation proximity.

Data comes from our own multi-venue aggregator (the same backend feed behind
``cascade_watch`` and ``funding_dislocation``), so every number on the card is
ours and provenance stays one hop away. Covered markets: BTC, ETH, SOL.

The card carries exactly one link: the GMGN perps CTA for the market with the
nearest 10x liquidation band (the actionable one). ``Referral.url`` is
``None`` with a ``reason`` when that market is unverified or the referral is
unconfigured — the payload on ``post.perps["cta"]`` mirrors that.

Fail-open: no data, no post. A recap is never invented from stale or partial
rows; an exception skips the tick and logs.

Perps rebate terms are NOT asserted (see ``docs/perps-lane.md``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.perps_card import render_perps_card
from rugmunchbot.referrals import gmgn_perps_referral

if TYPE_CHECKING:
    from rugmunchbot.channels.formatters._primitives import NewsPost

logger = logging.getLogger(__name__)

#: Covered markets, in display order.
RECAP_SYMBOLS: tuple[str, ...] = ("BTC", "ETH", "SOL")

#: Below this, an asset's OI is too thin to say anything honest about.
_MIN_OI_USD = 1e7


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


async def _fetch_rows() -> list[dict[str, Any]]:
    """Merge liquidation-map + funding rows for the covered markets."""
    from rugmunchbot.tools.cascade_predictor import cascade_watch
    from rugmunchbot.tools.funding_dislocation import funding_dislocation

    liq, fund = await asyncio.gather(
        cascade_watch(limit=10), funding_dislocation(limit=10), return_exceptions=True
    )
    liq_data = getattr(liq, "data", None)
    watchlist = (liq_data or {}).get("watchlist") or [] if isinstance(liq_data, dict) else []
    fund_data = getattr(fund, "data", None)
    signals = (fund_data or {}).get("signals") or [] if isinstance(fund_data, dict) else []
    funding_by_asset = {
        str(s.get("asset") or "").upper(): s for s in signals if isinstance(s, dict)
    }

    rows: list[dict[str, Any]] = []
    for row in watchlist:
        sym = str(row.get("asset") or "").upper()
        if sym not in RECAP_SYMBOLS:
            continue
        oi = _num(row.get("total_oi_usd"))
        if oi is None or oi < _MIN_OI_USD:
            continue
        sig = funding_by_asset.get(sym) or {}
        richest = sig.get("richest_funding") or {}
        rows.append(
            {
                "symbol": sym,
                "oi_usd": oi,
                "near_side": row.get("near_side"),
                "trigger_price": row.get("trigger_price_10x"),
                "distance_pct": _num(row.get("distance_pct")),
                "funding_rate": richest.get("rate"),
                "funding_venue": richest.get("venue"),
            }
        )
    rows.sort(key=lambda r: RECAP_SYMBOLS.index(r["symbol"]))
    return rows


def _commentary(rows: list[dict[str, Any]]) -> str:
    """One paragraph, derived only from the rows on the card."""
    with_dist = [r for r in rows if r["distance_pct"] is not None]
    if not with_dist:
        return "Liquidation proximity is unavailable this tick, so size as if the nearest stack is closer than it looks."
    near = min(with_dist, key=lambda r: r["distance_pct"] or 99.0)
    side = str(near.get("near_side") or "leverage").lower()
    rates = [r["funding_rate"] for r in rows if r.get("funding_rate") is not None]
    total_oi = sum(r["oi_usd"] for r in rows)
    if rates and all(x > 0 for x in rates):
        funding_read = "longs pay to hold on every covered venue"
    elif rates and all(x < 0 for x in rates):
        funding_read = "shorts pay to hold on every covered venue"
    elif rates:
        funding_read = "funding is split across venues"
    else:
        funding_read = "funding is flat or unpublished"
    return (
        f"{near['symbol']} carries the nearest stack: 10x {side} liquidations sit "
        f"{near['distance_pct']:.1f}% away at {near.get('trigger_price')}, inside a "
        f"routine intraday wick rather than a tail event. Across ${total_oi / 1e9:.1f}B "
        f"of covered open interest, {funding_read} \u2014 when the crowded side is "
        f"already paying, the cheaper move is the one that forces it out."
    )


async def build_perps_recap_post() -> NewsPost | None:
    """Render the daily perps card, or ``None`` when the feed cannot support it."""
    try:
        rows = await _fetch_rows()
    except Exception as exc:  # fail-open: a market feed outage is not a crash
        logger.warning("perps_recap:fetch_failed error=%s", str(exc)[:120])
        return None
    if not rows:
        logger.info("perps_recap:no_rows skipped")
        return None

    covered = [r for r in rows if r["distance_pct"] is not None]
    cta = min(covered, key=lambda r: r["distance_pct"]) if covered else rows[0]
    symbol = str(cta["symbol"])
    referral = gmgn_perps_referral(symbol)
    post = render_perps_card(
        rows, cta_symbol=symbol, referral=referral, commentary=_commentary(rows)
    )
    post.perps = {
        "symbols": [r["symbol"] for r in rows],
        "cta": {
            "symbol": symbol,
            "url": referral.url,
            "reason": referral.reason,
        },
    }
    return post


__all__ = ["RECAP_SYMBOLS", "build_perps_recap_post"]
