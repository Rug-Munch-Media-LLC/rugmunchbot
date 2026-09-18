"""market — pure formatting + render helpers (split from handlers/market.py, 2026-09-18)."""

from __future__ import annotations

import contextlib
import html
from typing import TYPE_CHECKING, Any

from rugmunchbot.config import fmt_pct
from rugmunchbot.formatters import (
    dexscreener_url,
    footer_links,
    sep,
    thin_sep,
)
from rugmunchbot.handlers._market_helpers import (
    _build_trending_kb,
    _render_trending_text,
    _window_change,
)
from rugmunchbot.handlers._trending import _fetch_fused_trending

from ._data import _cache_get_trending, _cache_set_trending

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup
_MOVERS_TF_LABEL = {"1h": "1H", "6h": "6H", "24h": "24H", "7d": "7D"}


async def render_trending(
    chain: str = "solana", limit: int = 20, window: str = "24h"
) -> tuple[str | None, InlineKeyboardMarkup | None]:
    """Fetch + render fused trending text and its action keyboard.

    Cache-aside on the fused token list (key ``trending:{chain}:{window}``,
    TTL 90s). The window does not change the underlying token list, but the
    spec key includes it so each window keeps its own entry; the caller's
    ``limit`` is applied on read so a ``limit=8`` inline path of a cached
    ``limit=20`` list still renders the top 8 exactly.
    """
    cached = await _cache_get_trending(chain, window)
    if cached is not None:
        cached_tokens = cached[:limit]
        return _render_trending_text(cached_tokens, chain, window), _build_trending_kb(
            cached_tokens, chain, window
        )

    tokens = await _fetch_fused_trending(chain, limit)
    if not tokens:
        return None, None
    await _cache_set_trending(chain, window, tokens)
    return _render_trending_text(tokens, chain, window), _build_trending_kb(tokens, chain, window)


def _fmt_usd(n: float | None) -> str:
    """Compact USD with trillions support (backend returns raw USD floats).

    Handles 3.2e12 → "$3.20T", 67_234 → "$67,234.10", 0.00001234 → "$0.00001234".
    Falls back to fmt_number conventions for everything else.
    """
    if n is None:
        return "N/A"
    try:
        v = float(n)
    except (TypeError, ValueError):
        return str(n)
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    if v >= 1e3:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:.4f}"
    if v >= 0.01:
        return f"${v:.6f}"
    return f"${v:.10f}"


def _change_emoji(change: float) -> str:
    return "\U0001f7e2" if change >= 0 else "\U0001f534"


def _mover_link(item: dict[str, Any]) -> str:
    """Tap-through URL for a mover: DexScreener for on-chain tokens,
    CoinGecko coin page for exchange-listed (CG) entries."""
    addr = item.get("address") or ""
    chain = (item.get("chain") or "solana").lower()
    if (item.get("source") or "").lower() == "gmgn" and addr:
        return dexscreener_url(addr, chain)
    if addr:
        return f"https://www.coingecko.com/en/coins/{addr}"
    return "https://www.coingecko.com/"


def _render_movers_lines(
    gainers: list[dict[str, Any]],
    losers: list[dict[str, Any]],
    timeframe: str,
    sources: list[str] | None = None,
) -> list[str]:
    """Render the movers message body (pure — no telegram objects)."""
    label = _MOVERS_TF_LABEL.get(timeframe, "24H")
    lines = [f"\U0001f680 <b>{label} Market Movers</b>", sep()]
    if sources:
        lines.append(f"\U0001f4e1 Sources: {', '.join(sources)}")
        lines.append("")

    lines.append("\U0001f7e2 <b>TOP GAINERS</b>")
    lines.append(thin_sep())
    for i, item in enumerate(gainers, 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:24]
        change = _window_change(item, timeframe)
        new_badge = " \U0001f195" if item.get("is_new_launch") else ""
        gmgn_tag = " (GMGN)" if (item.get("source") or "").lower() == "gmgn" else ""
        link = _mover_link(item)
        lines.append(
            f"{i}. {_change_emoji(change)} <b>{name}</b> ({symbol}){gmgn_tag}{new_badge}\n"
            f"   <b>{fmt_pct(change)}</b> | {_fmt_usd(item.get('price'))} | Vol: {_fmt_usd(item.get('volume_24h'))}"
            f' | \U0001f517 <a href="{link}">Chart</a>'
        )
        # Paragraph break between mover cards so multi-line entries never
        # blur into a wall of text.
        if i < len(gainers):
            lines.append("")
    if not gainers:
        lines.append("<i>No gainers in the current window.</i>")

    lines.append("")
    lines.append("\U0001f534 <b>TOP LOSERS</b>")
    lines.append(thin_sep())
    for i, item in enumerate(losers, 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:24]
        change = _window_change(item, timeframe)
        link = _mover_link(item)
        lines.append(
            f"{i}. {_change_emoji(change)} <b>{name}</b> ({symbol})\n"
            f"   <b>{fmt_pct(change)}</b> | {_fmt_usd(item.get('price'))} | Vol: {_fmt_usd(item.get('volume_24h'))}"
            f' | \U0001f517 <a href="{link}">Chart</a>'
        )
        if i < len(losers):
            lines.append("")
    if not losers:
        lines.append("<i>No losers in the top movers \u2014 market is green \U0001f7e2</i>")
    lines.append(footer_links())
    return lines


def _whale_flow_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate buy/sell flow stats from whale feed items.

    Returns buy/sell USD volumes, net flow, buy/sell ratio and a
    confidence verdict (bullish / bearish / neutral). Pure.
    """
    buy_vol = 0.0
    sell_vol = 0.0
    buys = 0
    sells = 0
    for item in items:
        try:
            usd = float(item.get("value_usd") or 0)
        except (TypeError, ValueError):
            usd = 0.0
        if (item.get("type") or "buy").lower() == "sell":
            sell_vol += usd
            sells += 1
        else:
            buy_vol += usd
            buys += 1
    net = buy_vol - sell_vol
    ratio = buy_vol / sell_vol if sell_vol > 0 else None
    if net > 0:
        verdict = "\U0001f7e2 BULLISH \u2014 whales are net ACCUMULATING"
    elif net < 0:
        verdict = "\U0001f534 BEARISH \u2014 whales are net DISTRIBUTING"
    else:
        verdict = "\u26aa NEUTRAL \u2014 balanced two-way flow"
    return {
        "buy_vol": buy_vol,
        "sell_vol": sell_vol,
        "net": net,
        "ratio": ratio,
        "buys": buys,
        "sells": sells,
        "verdict": verdict,
    }


def _whale_analysis_bits(item: dict[str, Any]) -> list[str]:
    """Per-whale intelligence bits: funding source, wallet age, pattern.

    Reads the enrichment fields the backend whale feed writes when
    available; falls back to the smart-money flag. Pure.
    """
    bits: list[str] = []
    funding = item.get("funding_source") or item.get("funded_by") or item.get("exchange") or ""
    if funding:
        bits.append(f"funding: {html.escape(str(funding)[:40])}")
    age = item.get("wallet_age_days")
    if age is not None:
        with contextlib.suppress(TypeError, ValueError):
            bits.append(f"wallet age: {int(age)}d")
    pattern = (
        item.get("historical_pattern")
        or item.get("pattern")
        or ("known accumulator" if item.get("is_smart_money") else "")
    )
    if pattern:
        bits.append(html.escape(str(pattern)[:48]))
    return bits
