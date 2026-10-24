"""Paid placement (boosted) block for /trending — disclosed and unranked.

DexScreener token-boosts are PAID placements: an issuer pays to be promoted.
They are split out of the organic list before ranking and rendered in their
own block with an explicit disclosure, because paid placement must never move
an organic trending rank.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.config import fmt_number, fmt_pct
from rugmunchbot.handlers._market_helpers._badges import _chain_badge, _fmt_price
from rugmunchbot.handlers._market_helpers._names import _trending_display
from rugmunchbot.handlers._market_helpers._tokens import _window_change

BOOST_DISCLOSURE = "Paid placement \u2014 does not affect trending rank"
_MAX_BOOST_ROWS = 5
GREEN = "\U0001f7e2"
RED = "\U0001f534"


def _split_paid(
    tokens: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a trending list into (organic, paid-placement) halves."""
    organic: list[dict[str, Any]] = []
    paid: list[dict[str, Any]] = []
    for token in tokens:
        (paid if token.get("paid_placement") else organic).append(token)
    return organic, paid


def _boosted_entry(token: dict[str, Any], chain: str, window: str) -> str:
    """One boosted-token line: name, chain, price, change, volume, link."""
    name, symbol = _trending_display(token)
    addr = (token.get("address") or "").strip()
    display_name = name if name not in ("Unknown", "???", "") else f"{addr[:8]}...{addr[-6:]}"
    display_symbol = symbol if symbol not in ("Unknown", "???", "") else "?"
    chg = _window_change(token, window)
    price_txt = _fmt_price(token.get("price_usd") or token.get("price"))
    chg_emoji = GREEN if chg >= 0 else RED
    parts = [
        _chain_badge(token.get("chain") or chain),
        price_txt,
        f"{chg_emoji} {fmt_pct(chg)}",
        fmt_number(token.get("volume_24h") or 0),
    ]
    body = " \u00b7 ".join(part for part in parts if part)
    label = f"<b>{html.escape(display_name)}</b> ({html.escape(display_symbol.upper())})"
    if addr:
        label = f'<a href="https://dexscreener.com/{chain}/{addr}">{label}</a>'
    return f"\u2022 {label}\n   {body}"


def _render_boosted_lines(paid: list[dict[str, Any]], chain: str, window: str) -> list[str]:
    """Disclosed paid-placement block (empty list when nothing is paid)."""
    if not paid:
        return []
    lines = ["", "\U0001f680 <b>Boosted</b>", f"<i>{BOOST_DISCLOSURE}</i>"]
    for token in paid[:_MAX_BOOST_ROWS]:
        lines.append(_boosted_entry(token, token.get("chain") or chain, window))
    return lines
