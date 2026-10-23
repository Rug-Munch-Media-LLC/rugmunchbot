"""Render one trending / launchpad call as a channel post."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.smart_money._fmt import esc, short, usd
from rugmunchbot.smart_money.render_base import compose

if TYPE_CHECKING:
    from rugmunchbot.channels.formatters._primitives import NewsPost
    from rugmunchbot.smart_money.types import Feed

_RISK = (
    "a launch call is not a buy signal — most new tokens fail or rug, and "
    "liquidity can be pulled at any time. Verify the contract and LP lock "
    "before acting. Not financial advice."
)


def render_call(feed: Feed, item: dict[str, Any]) -> NewsPost:
    """Build the five-block post for one launch (absent fields stay absent)."""
    symbol = esc(item.get("symbol") or "?")
    name = esc(str(item.get("name") or "")[:40])
    headline = f"🚀 <b>LAUNCH CALL · {symbol}</b>" + (f" — {name}" if name else "")

    evidence: list[str] = []
    if item.get("chain"):
        evidence.append(f"Chain: {esc(item['chain'])}")
    if item.get("liquidity_usd") is not None:
        evidence.append(f"Liquidity: <b>{usd(item['liquidity_usd'])}</b>")
    if item.get("holders") is not None:
        evidence.append(f"Holders: {esc(item['holders'])}")
    if item.get("age_hours") is not None:
        evidence.append(f"Age: {esc(item['age_hours'])}h")
    if item.get("risk_level"):
        evidence.append(f"Scanner risk: {esc(item['risk_level'])}")
    flags = [str(f) for f in (item.get("risk_flags") or [])[:4]]
    if flags:
        evidence.append(f"Flags: {esc(', '.join(flags))}")
    if item.get("address"):
        evidence.append(f"Contract: <code>{esc(short(item['address'], 8, 6))}</code>")

    return compose(
        headline=headline,
        evidence=evidence,
        source=feed.source or "unknown",
        updated=feed.updated_at,
        risk=_RISK,
    )
