"""HTML trending list renderer + trending keyboard builder.

Pure rendering: telegram keyboard primitives and ``config`` / ``formatters``
helpers only. Split out of ``handlers/_market_helpers.py`` (2026-10-23).
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import WEBSITE_URL, fmt_number, fmt_pct
from rugmunchbot.formatters import footer_links, sep
from rugmunchbot.handlers._market_helpers._badges import (
    TRENDING_CHAINS,
    _chain_badge,
    _fmt_price,
    _liq_badge,
    _risk_emoji,
)
from rugmunchbot.handlers._market_helpers._names import _trending_display
from rugmunchbot.handlers._market_helpers._rank import _sort_trending, _window_note
from rugmunchbot.handlers._market_helpers._tokens import (
    TRENDING_WINDOWS,
    _age_badge,
    _window_change,
)

_MAX_TRENDING_ROWS = 10


def _render_trending_text(tokens: list[dict[str, Any]], chain: str, window: str) -> str:
    """HTML trending list with badges, clickable CAs, risk indicators."""
    tokens = _sort_trending(tokens, window)
    lines = [f"🔥 <b>Trending — {chain.capitalize()} · {window}</b>", sep()]

    rendered = 0
    for t in tokens:
        addr = t.get("address", "")
        if not addr:
            continue
        name, symbol = _trending_display(t)
        rendered += 1
        if rendered > _MAX_TRENDING_ROWS:
            break
        # Paragraph break between token cards so multi-line entries never
        # blur into a wall of text.
        if rendered > 1:
            lines.append("")
        chg = _window_change(t, window)
        vol = float(t.get("volume_24h") or 0)
        liq = float(t.get("liquidity_usd") or 0)
        risk = t.get("risk_score")
        created = t.get("created_at")

        # Badges — per-token chain so the "all" view shows each token's home chain
        chain_badge = _chain_badge(t.get("chain") or chain)
        age_badge = _age_badge(created)
        liq_badge = _liq_badge(liq)
        risk_emoji = _risk_emoji(risk)
        chg_emoji = "🟢" if chg >= 0 else "🔴"
        price_txt = _fmt_price(t.get("price_usd") or t.get("price"))

        # Bundle warning
        bundle_flag = " 🚨" if t.get("bundle_detected") else ""

        # If name unknown, show truncated address instead
        display_name = name if name not in ("Unknown", "???", "") else f"{addr[:8]}...{addr[-6:]}"
        display_symbol = symbol if symbol not in ("Unknown", "???", "") else "?"
        price_part = f"Price: {price_txt} | " if price_txt else ""
        lines.append(
            f"{rendered}. {chain_badge} {age_badge} {risk_emoji} <b>{display_name}</b> ({display_symbol}){bundle_flag}\n"
            f"   {liq_badge} | {chg_emoji} {fmt_pct(chg)} | {price_part}Vol: {fmt_number(vol)}\n"
            f"   <code>{addr}</code>"
        )

    note = _window_note(window, tokens)
    if note:
        lines.append("")
        lines.append(f"<i>{note}</i>")
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

    # Token buttons — one per token, deep link to rugcharts chart page
    for t in tokens[:_MAX_TRENDING_ROWS]:
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
    chain_list = ["solana", "ethereum", "bsc", "base", "all"]
    chain_label = {"solana": "SOL", "ethereum": "ETH", "bsc": "BSC", "base": "BASE", "all": "ALL"}
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
