"""Render one Hyperliquid event (mover, squeeze, liquidation cluster)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.channels.formatters._primitives import NewsPost

from ._fmt import esc, usd
from .render_base import compose

_RISK = (
    "leveraged perps — a funding flip can force unwinds. Market-level data "
    "(asset, size, funding, OI); no wallet identity, no trader attribution."
)


def _funding(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.4f}%/h"
    except (TypeError, ValueError):
        return "n/a"


def _headline(item: dict[str, Any]) -> str:
    asset = esc(item.get("asset") or "?")
    kind = item.get("kind")
    if kind == "mover":
        direction = "PUMP" if item.get("direction") == "up" else "DUMP"
        try:
            change = f"{float(item.get('change_24h_pct')):+.1f}%"
        except (TypeError, ValueError):
            change = "n/a"
        return f"🐋 <b>HYPERLIQUID · {asset} {direction} {change}</b>"
    if kind == "squeeze":
        side = "LONGS" if item.get("direction") == "long_squeeze" else "SHORTS"
        return f"⚡ <b>FUNDING · {asset} {side} PAYING</b>"
    return f"💥 <b>LIQUIDATION MAP · {asset}</b>"


def render_hl(feed: Any, item: dict[str, Any]) -> NewsPost:
    """Build the post for one normalised Hyperliquid event."""
    kind = item.get("kind")
    evidence: list[str] = []
    if kind == "mover":
        evidence.append(
            f"24h volume <b>{usd(item.get('volume_24h'))}</b> · OI {usd(item.get('open_interest_usd'))}"
        )
        evidence.append(f"Funding: {_funding(item.get('funding_rate'))}")
        if item.get("price") is not None:
            evidence.append(f"Mark: ${esc(item.get('price'))}")
    elif kind == "squeeze":
        evidence.append(f"Funding: <b>{_funding(item.get('funding_rate'))}</b>")
        evidence.append(f"Open interest: {usd(item.get('open_interest_usd'))}")
    else:
        evidence.append(
            f"Open interest <b>{usd(item.get('open_interest_usd'))}</b> · OI share {esc(item.get('oi_share_pct'))}%"
        )
        evidence.append(
            f"Liquidation pressure {usd(item.get('liquidation_pressure'))} · "
            f"10x long liq ${esc(item.get('long_liq_price_10x'))} / short ${esc(item.get('short_liq_price_10x'))}"
        )
        evidence.append(f"Funding: {_funding(item.get('funding_rate'))}")

    return compose(
        headline=_headline(item),
        evidence=evidence,
        source="Hyperliquid metaAndAssetCtxs via rmi-backend",
        updated=feed.updated_at,
        risk=_RISK,
    )
