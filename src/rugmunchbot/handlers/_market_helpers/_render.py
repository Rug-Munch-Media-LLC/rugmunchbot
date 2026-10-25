"""HTML trending list renderer + trending keyboard builder.

Pure rendering: telegram keyboard primitives and ``config`` / ``formatters``
helpers only. Split out of ``handlers/_market_helpers.py`` (2026-10-23).
"""

from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import WEBSITE_URL
from rugmunchbot.formatters import footer_links, sep
from rugmunchbot.handlers._market_helpers._badges import (
    TRENDING_CHAIN_LIST,
    TRENDING_CHAINS,
    _is_trending_chain,
)
from rugmunchbot.handlers._market_helpers._boosted import (
    _render_boosted_lines,
    _split_paid,
)
from rugmunchbot.handlers._market_helpers._card import _render_token_card
from rugmunchbot.handlers._market_helpers._names import _trending_display
from rugmunchbot.handlers._market_helpers._rank import _sort_trending, _window_note
from rugmunchbot.handlers._market_helpers._tokens import TRENDING_WINDOWS

_MAX_TRENDING_ROWS = 10


def _render_trending_text(tokens: list[dict[str, Any]], chain: str, window: str) -> str:
    """HTML trending list — ranked organic cards, then the disclosed paid block.

    Paid placements (DexScreener token-boosts) are removed BEFORE ranking, so
    a boost can never move an organic rank; they render in their own block
    with the "paid placement" disclosure, never as a numbered entry.
    """
    organic, paid = _split_paid(tokens)
    ranked = _sort_trending(organic, window)
    lines = [
        f"\U0001f525 <b>Trending \u2014 {html.escape(chain.capitalize())} \u00b7 {window}</b>",
        sep(),
    ]
    if not _is_trending_chain(chain):
        # Malformed chain arg: say so instead of pretending the feed is empty.
        lines.append(
            f"<i>Unknown chain \u201c{html.escape(chain)}\u201d \u2014 pick a chain below.</i>"
        )
        lines.append("")

    rendered = 0
    for token in ranked:
        if not token.get("address"):
            continue
        rendered += 1
        if rendered > _MAX_TRENDING_ROWS:
            break
        # Paragraph break between token cards so multi-line entries never
        # blur into a wall of text.
        if rendered > 1:
            lines.append("")
        lines.extend(_render_token_card(rendered, token, chain, window))

    if rendered == 0:
        lines.append("<i>No organic trending tokens right now.</i>")

    note = _window_note(window, ranked)
    if note:
        lines.append("")
        lines.append(f"<i>{note}</i>")
    lines.extend(_render_boosted_lines(paid, chain, window))
    lines.append(f"\n<i>Chains: {TRENDING_CHAINS}</i>")
    lines.append(footer_links())
    return "\n".join(lines)


def _build_trending_kb(
    tokens: list[dict[str, Any]], chain: str, window: str
) -> InlineKeyboardMarkup:
    """Trending keyboard: rugcharts deep-link token buttons + chain/window selectors.

    Design: each token gets ONE button that deep-links to the RugCharts web
    chart (``rugmunch.io/charts?chain=X&address=Y``). The chain row switches
    chain, the window row changes the time frame. Clean, scannable, no
    button overload.
    """
    rows: list[list[InlineKeyboardButton]] = []

    # Token buttons — one per ORGANIC token, deep link to the rugcharts chart
    # page. Paid placements get no button: the keyboard mirrors the ranking.
    for t in _split_paid(tokens)[0][:_MAX_TRENDING_ROWS]:
        addr = t.get("address", "")
        if not addr:
            continue
        name, symbol = _trending_display(t)
        # If name/symbol are unknown, use truncated address
        if name in ("Unknown", "???", "") or symbol in ("Unknown", "???", ""):
            label = f"📈 {addr[:8]}...{addr[-6:]}"[:40]
        else:
            label = f"📈 {name} ({symbol})"[:40]
        tok_chain = (t.get("chain") or chain).lower()
        rows.append(
            [
                InlineKeyboardButton(
                    label, url=f"{WEBSITE_URL}/charts?chain={tok_chain}&address={addr}"
                )
            ]
        )

    # Chain selector row: [SOL] [ETH] [BSC] [BASE] [ALL]
    chain_list = list(TRENDING_CHAIN_LIST)
    chain_label = {
        "solana": "SOL",
        "ethereum": "ETH",
        "bsc": "BSC",
        "base": "BASE",
        "arbitrum": "ARB",
        "all": "ALL",
    }
    rows.append(
        [
            InlineKeyboardButton(
                f"[{chain_label[c]}]" if c == chain else chain_label[c],
                callback_data=f"trending_chain_{c}",
            )
            for c in chain_list
        ]
    )

    # Window selector row
    win_row = [
        InlineKeyboardButton(
            f"[{w}]" if w == window else w, callback_data=f"trending_win_{chain}_{w}"
        )
        for w in TRENDING_WINDOWS
    ]
    rows.append(win_row)

    # Refresh + Menu
    rows.append(
        [
            InlineKeyboardButton("🔄 Refresh", callback_data=f"trending_refresh_{chain}"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu_main"),
        ]
    )
    return InlineKeyboardMarkup(rows)
