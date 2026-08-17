"""Capability menu for RugMunchBot — clean, discoverable keyboard.

Data-driven capability list: extend it by appending a tuple, no new code.
``capability_menu_kb()`` renders the two-column InlineKeyboardMarkup and
``capability_help_text()`` renders the plain-HTML capability card.

The ``callback`` values reuse the bot's EXISTING callback surfaces so every
button works with zero callback-handler changes:
  * ``run_*`` buttons are handled by ``handlers/callback._dispatch_run_cmd``
    (live inline data for trending/news/market/whales; usage cards for
    scan/price/chart/ta/polymarket/smartmoney),
  * ``menu_cat_chat`` shows the /chat welcome card (Ask knowledge),
  * ``menu_main`` returns to the main menu.

Capabilities: Scan · Trending by chain · Price · Chart · TA (incl. chart
patterns) · Prediction markets · Market Condition · News · Alpha & whales ·
Smart money · Ask knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class Capability:
    """One capability entry: button label, help description, callback."""

    label: str
    description: str
    callback: str


# Single source of truth for the capability menu. Append to extend — the
# keyboard builder and the help card render from this tuple automatically.
# v12 (2026-08-14): reflects the newest live surfaces — Chart (chart image)
# and chart-pattern signals folded into the TA entry (the technical_analysis
# tool is the chart-patterns surface; there is no standalone patterns
# callback). Ordering = most-used first (Scan → markets → intel). Every
# callback below is handled by the EXISTING dispatchers (``run_*`` inline
# data or usage cards; ``menu_cat_chat`` for Ask knowledge).
CAPABILITIES: tuple[Capability, ...] = (
    Capability("\U0001f50d Scan", "Full token risk audit \u2014 paste an address", "run_scan"),
    Capability("\U0001f525 Trending by chain", "Trending tokens, filterable per chain", "run_trending"),
    Capability("\U0001f4b0 Price", "Live price for $SYMBOL or an address", "run_price"),
    Capability("\U0001f4c8 Chart", "Chart image with indicators & trend analysis", "run_chart"),
    Capability("\U0001f4ca TA", "Technical analysis, chart patterns & signals", "run_ta"),
    Capability("\U0001f52e Prediction markets", "Polymarket odds & election markets", "run_polymarket"),
    Capability("\U0001f321\ufe0f Market Condition", "Macro read: cap, fear & greed, movers", "run_market"),
    Capability("\U0001f4f0 News", "Live crypto headlines & sentiment", "run_news"),
    Capability("\U0001f40b Alpha & Whales", "Whale flows & big-money moves", "run_whales"),
    Capability("\U0001f9e0 Smart money", "Top traders & whale flows", "run_smartmoney"),
    Capability("\U0001f4da Ask knowledge", "RAG knowledge-base Q&A", "menu_cat_chat"),
)


def capability_menu_kb(columns: int = 2) -> InlineKeyboardMarkup:
    """Two-column capability keyboard with a Back-to-menu row.

    ``columns`` controls the grid width; the last partial row is kept as-is,
    so odd-sized menus stay balanced.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(CAPABILITIES), columns):
        row = [InlineKeyboardButton(c.label, callback_data=c.callback) for c in CAPABILITIES[i : i + columns]]
        rows.append(row)
    rows.append([InlineKeyboardButton("\U0001f3e0 Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def capability_help_text() -> str:
    """Plain-HTML capability card (safe to send with ParseMode.HTML)."""
    lines = ["\U0001f680 <b>What can Rug Munch Bot do?</b>", ""]
    for c in CAPABILITIES:
        lines.append(f"\u2022 {c.label} \u2014 {c.description}")
    lines.append("")
    lines.append(
        "Just <i>ask in plain text</i> \u2014 e.g. \u201cprice of $BONK\u201d, \u201cis this address safe?\u201d"
    )
    return "\n".join(lines)


def capability_by_callback(callback: str) -> Capability | None:
    """Look up a capability by its callback (handy for menu-state routing)."""
    for c in CAPABILITIES:
        if c.callback == callback:
            return c
    return None


__all__ = [
    "CAPABILITIES",
    "Capability",
    "capability_by_callback",
    "capability_help_text",
    "capability_menu_kb",
]
