"""One trending token card — rank, icon, badges, market fields, score reason.

Extracted from ``_render.py`` (2026-10-24) so the list renderer stays a thin
ranking loop. Pure formatting: token dict in, HTML lines out. Every field is
either carried by the token or omitted — nothing is invented, and no HTTP
request is made (icons are links, never downloads).
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.config import fmt_number, fmt_pct
from rugmunchbot.handlers._market_helpers._badges import (
    _chain_badge,
    _fmt_price,
    _liq_badge,
    _risk_emoji,
)
from rugmunchbot.handlers._market_helpers._icons import _icon_slot
from rugmunchbot.handlers._market_helpers._names import _trending_display
from rugmunchbot.handlers._market_helpers._score import _score_line
from rugmunchbot.handlers._market_helpers._tokens import _age_badge, _window_change


def _render_token_card(rank: int, token: dict[str, Any], chain: str, window: str) -> list[str]:
    """Render one numbered card: rank, icon, name/symbol, price, change, vol, liq, score."""
    addr = token.get("address", "")
    name, symbol = _trending_display(token)
    display_name = name if name not in ("Unknown", "???", "") else f"{addr[:8]}...{addr[-6:]}"
    display_symbol = symbol if symbol not in ("Unknown", "???", "") else "?"
    chg = _window_change(token, window)
    price_txt = _fmt_price(token.get("price_usd") or token.get("price"))
    volume = fmt_number(token.get("volume_24h") or 0)
    liquidity = fmt_number(token.get("liquidity_usd") or 0)
    chg_emoji = "\U0001f7e2" if chg >= 0 else "\U0001f534"
    bundle_flag = " \U0001f6a8" if token.get("bundle_detected") else ""

    fields = [
        _liq_badge(float(token.get("liquidity_usd") or 0)),
        f"{chg_emoji} {fmt_pct(chg)}",
        f"Price: {price_txt}" if price_txt else "",
        f"Vol: {volume}",
        f"Liq: {liquidity}",
    ]
    head = (
        f"{rank}. {_icon_slot(token)} {_chain_badge(token.get('chain') or chain)} "
        f"{_age_badge(token.get('created_at'))} {_risk_emoji(token.get('risk_score'))} "
        f"<b>{html.escape(display_name)}</b> ({html.escape(display_symbol.upper())}){bundle_flag}"
    )
    lines = [head, "   " + " | ".join(field for field in fields if field)]
    lines.append(f"   {_score_line(token)}")
    lines.append(f"   <code>{html.escape(addr)}</code>")
    return lines
