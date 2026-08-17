"""Inline market/news/sentiment renderers for callback buttons.

Extracted from ``callback.py`` during the Batch 11 de-godding pass: the live
inline renderers behind ``run_market``/``run_movers``/``run_launches``/
``run_whales``/``run_trending``/``run_news``/``run_sentiment`` refresh buttons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, COINGECKO, backend_headers, fmt_number
from app.domains.telegram.rugmunchbot.data_resolver import resolve_fear_greed, resolve_market_global
from app.domains.telegram.rugmunchbot.formatters import footer_links, sep, thin_sep
from app.domains.telegram.rugmunchbot.keyboards import back_kb, token_buttons
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json

if TYPE_CHECKING:
    from telegram import Update

logger = get_logger("rmi_bot")


async def _inline_trending(update: Update, ctx) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.edit_message_text("\U0001f525 <b>Fetching trending...</b>", parse_mode=ParseMode.HTML)
    from app.domains.telegram.rugmunchbot.handlers.market import render_trending

    text, kb = await render_trending("solana", limit=8, window="24h")
    if text is None or kb is None:
        await query.edit_message_text(
            "\u274c Could not fetch trending data.", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
        return
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def _inline_market_data(update: Update, ctx, kind: str) -> None:
    """Live inline market data for run_market/run_movers/run_launches/run_whales.

    Reuses the backend market-intel fetch helpers from market.py (the same
    wiring as the /market, /movers, /launches and /whales commands) so menu
    buttons show live data instead of usage cards.
    """
    from app.domains.telegram.rugmunchbot.handlers.market import (
        _change_emoji,
        _fetch_market_intel,
        _fmt_usd,
    )

    query = update.callback_query
    if query is None:
        return

    labels = {
        "market": ("\U0001f4ca", "Fetching market overview...", "market_refresh"),
        "movers": ("\U0001f680", "Fetching market movers...", "movers_refresh"),
        "launches": ("\U0001f195", "Fetching new launches...", "launches_refresh"),
        "whales": ("\U0001f40b", "Fetching whale activity...", "whales_refresh"),
    }
    emoji, busy_text, refresh_cb = labels[kind]
    await query.edit_message_text(f"{emoji} <b>{busy_text}</b>", parse_mode=ParseMode.HTML)

    if kind == "market":
        text = await _inline_market_overview(_fetch_market_intel, _fmt_usd)
        token_items: list[dict[str, Any]] = []
    elif kind == "movers":
        text = await _inline_movers_text(_fetch_market_intel, _fmt_usd, _change_emoji)
        token_items = []
    elif kind == "launches":
        text, token_items = await _inline_launches_text(_fetch_market_intel, _fmt_usd, _change_emoji)
    else:
        text, token_items = await _inline_whales_text(_fetch_market_intel, _fmt_usd)

    if text is None:
        await query.edit_message_text(
            f"\u274c Could not fetch {kind} data.", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
        return
    kb_rows: list[list[InlineKeyboardButton]] = []
    for item in token_items:
        addr = item.get("address") or item.get("token_address") or ""
        if addr:
            kb_rows.append(token_buttons(addr, item.get("chain") or "solana"))
    kb_rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data=refresh_cb)])
    kb_rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb_rows),
        disable_web_page_preview=True,
    )


async def _inline_market_overview(fetch, fmt_usd) -> str | None:
    """Render the /market overview as text for inline callbacks."""
    import asyncio

    overview, macro = await asyncio.gather(
        fetch("/api/v1/market/overview"),
        fetch("/api/v1/market/macro"),
    )
    if not overview:
        return None

    from app.domains.telegram.rugmunchbot.config import fmt_pct

    lines = ["\U0001f4ca <b>Market Overview</b>", sep()]
    if overview.get("total_market_cap") is not None:
        lines.append(f"\U0001f310 Total Market Cap: <b>{fmt_usd(overview.get('total_market_cap'))}</b>")
    if overview.get("total_volume_24h") is not None:
        lines.append(f"\U0001f4c8 24h Volume: <b>{fmt_usd(overview.get('total_volume_24h'))}</b>")
    if overview.get("btc_dominance") is not None:
        lines.append(f"\u20bf BTC Dominance: <b>{float(overview['btc_dominance']):.1f}%</b>")
    if overview.get("eth_dominance") is not None:
        lines.append(f"\u27e0 ETH Dominance: <b>{float(overview['eth_dominance']):.1f}%</b>")
    active = overview.get("active_cryptocurrencies")
    if active:
        import contextlib

        with contextlib.suppress(TypeError, ValueError):
            lines.append(f"\U0001fa99 Active Coins: <b>{int(active):,}</b>")

    fg = overview.get("fear_greed") or {}
    if fg.get("value") is not None:
        fg_value = int(fg.get("value", 0))
        fg_class = fg.get("classification") or "Neutral"
        fg_emoji = (
            "\U0001f525"
            if fg_value >= 75
            else "\U0001f60d"
            if fg_value >= 60
            else "\U0001f610"
            if fg_value >= 40
            else "\U0001f631"
        )
        lines.append("")
        lines.append(f"{fg_emoji} Fear & Greed: <b>{fg_value}</b> ({fg_class})")

    if macro and macro.get("items"):
        lines.append("")
        for it in macro["items"][:2]:
            if it.get("current_price") is None:
                continue
            asset = it.get("asset", "")
            change = it.get("change_24h")
            change_txt = f" ({fmt_pct(change)})" if change is not None else ""
            lines.append(f"\U0001f4b0 <b>{asset}:</b> {fmt_usd(it.get('current_price'))}{change_txt}")

    lines.append(footer_links())
    return "\n".join(lines)


async def _inline_movers_text(fetch, fmt_usd, change_emoji) -> str | None:
    """Render the /movers report as text for inline callbacks."""
    from app.domains.telegram.rugmunchbot.config import fmt_pct

    data = await fetch("/api/v1/market/movers", params={"limit": 20})
    items = (data or {}).get("items") or []
    if not items:
        return None

    gainers = [i for i in items if (i.get("change_24h") or 0) >= 0][:6]
    losers = [i for i in items if (i.get("change_24h") or 0) < 0][:6]

    lines = ["\U0001f680 <b>24h Market Movers</b>", sep()]
    lines.append("\U0001f7e2 <b>TOP GAINERS</b>")
    lines.append(thin_sep())
    for i, item in enumerate(gainers, 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:24]
        change = item.get("change_24h") or 0
        lines.append(
            f"{i}. {change_emoji(change)} <b>{name}</b> ({symbol})\n"
            f"   <b>{fmt_pct(change)}</b> | {fmt_usd(item.get('price'))} | Vol: {fmt_usd(item.get('volume_24h'))}"
        )
    if not gainers:
        lines.append("<i>No gainers in the current window.</i>")

    lines.append("")
    lines.append("\U0001f534 <b>TOP LOSERS</b>")
    lines.append(thin_sep())
    for i, item in enumerate(losers, 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:24]
        change = item.get("change_24h") or 0
        lines.append(
            f"{i}. {change_emoji(change)} <b>{name}</b> ({symbol})\n"
            f"   <b>{fmt_pct(change)}</b> | {fmt_usd(item.get('price'))} | Vol: {fmt_usd(item.get('volume_24h'))}"
        )
    if not losers:
        lines.append("<i>No losers in the top movers \u2014 market is green \U0001f7e2</i>")

    lines.append(footer_links())
    return "\n".join(lines)


async def _inline_launches_text(fetch, fmt_usd, change_emoji) -> tuple[str | None, list[dict[str, Any]]]:
    """Render the /launches report as text for inline callbacks.

    Returns ``(text, token_items)`` so callers can attach per-token buttons.
    """
    from app.domains.telegram.rugmunchbot.config import fmt_chain, fmt_pct

    data = await fetch("/api/v1/market/launches", params={"limit": 8})
    items = (data or {}).get("items") or []
    if not items:
        return None, []

    lines = ["\U0001f195 <b>New Token Launches</b>", sep()]
    for i, item in enumerate(items[:8], 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:28]
        change = item.get("change_24h") or 0
        chain_txt = fmt_chain(item.get("chain") or "solana")
        bundler = "\U0001f6a8 bundler" if item.get("bundler_detected") else ""
        addr = item.get("address") or ""
        lines.append(
            f"{i}. {change_emoji(change)} <b>{name}</b> ({symbol}) {chain_txt}\n"
            f"   {fmt_usd(item.get('price'))} | <b>{fmt_pct(change)}</b> | Vol: {fmt_usd(item.get('volume_24h'))}"
            f"{f' | {bundler}' if bundler else ''}"
        )
        if addr:
            lines.append(f"   <code>{addr}</code>")
    lines.append("\n<i>Fresh launches \u2014 always /scan before buying.</i>")
    lines.append(footer_links())
    return "\n".join(lines), items[:8]


async def _inline_whales_text(fetch, fmt_usd) -> tuple[str | None, list[dict[str, Any]]]:
    """Render the /whales report as text for inline callbacks.

    Returns ``(text, token_items)`` so callers can attach per-token buttons.
    """
    from app.domains.telegram.rugmunchbot.config import fmt_chain

    data = await fetch("/api/v1/market/whales", params={"limit": 5})
    items = (data or {}).get("items") or []
    if not items:
        return None, []

    lines = ["\U0001f40b <b>Whale Activity</b>", sep()]
    for i, item in enumerate(items[:5], 1):
        symbol = (item.get("token_symbol") or "???").upper()[:12]
        action = (item.get("type") or "buy").lower()
        action_txt = "BUY" if action == "buy" else "SELL"
        action_emoji = "\U0001f7e2" if action == "buy" else "\U0001f534"
        smart = " \U0001f4a1 smart money" if item.get("is_smart_money") else ""
        chain_raw = item.get("chain") or ""
        chain_txt = f" {fmt_chain(chain_raw)}" if chain_raw else ""
        addr = item.get("token_address") or ""
        lines.append(
            f"{i}. {action_emoji} <b>{action_txt} {symbol}</b>{chain_txt}{smart}\n"
            f"   {fmt_usd(item.get('value_usd'))}"
            f"{f' | <code>{addr}</code>' if addr else ''}"
        )
    lines.append("\n<i>Large moves detected across Solana/Ethereum/Bitcoin pairs.</i>")
    lines.append(footer_links())
    return "\n".join(lines), items[:5]


async def _inline_news(update: Update, ctx) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.edit_message_text("\U0001f4f0 <b>Fetching news...</b>", parse_mode=ParseMode.HTML)
    articles: list[dict[str, Any]] = []
    source_label = "Backend"
    body = await http_get_json(
        f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5",
        headers=backend_headers(),
        timeout=10,
    )
    if body:
        articles = body.get("items", [])
    else:
        logger.warning("inline news backend failed")
    if not articles:
        from app.domains.telegram.rugmunchbot.handlers.market import _fetch_cg_trending_news

        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "Trending"
    if not articles:
        await query.edit_message_text(
            "\U0001f4f0 <b>Crypto News</b>\n\nNews feed temporarily unavailable.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    lines = ["\U0001f4f0 <b>Crypto News</b>", f"<i>via {source_label}</i>", sep()]
    for a in articles[:5]:
        title = (a.get("title") or "Untitled")[:100]
        source = a.get("source", "News")
        url = a.get("url") or a.get("link") or ""
        title_linked = f'<a href="{url}">{title}</a>' if url else title
        lines.append(f"\U0001f4f0 {title_linked}\n  <i>{source}</i>")
    lines.append(footer_links())
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        ),
        disable_web_page_preview=True,
    )


async def _inline_sentiment(update: Update, ctx) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.edit_message_text("\U0001f4c8 <b>Fetching sentiment...</b>", parse_mode=ParseMode.HTML)
    try:
        lines = ["\U0001f4c8 <b>Market Sentiment</b>", thin_sep()]

        # Own data first: backend market intel (cached). Only when the backend
        # is unreachable do we fall back to alternative.me + CoinGecko direct.
        # DATA_SOURCE is logged by the resolver for reliance tracking.

        async def _external_fng() -> dict[str, Any] | None:
            fg = await http_get_json("https://api.alternative.me/fng/?limit=1", timeout=12)
            if fg is None:
                return None
            fg_data = fg.get("data", [{}])[0]
            return {
                "value": int(fg_data.get("value", 50)),
                "classification": fg_data.get("value_classification", "Neutral"),
            }

        fng = await resolve_fear_greed(_external_fng)
        if fng and fng.get("value"):
            lines.append(
                f"\U0001f610 Fear & Greed: <b>{fng.get('value')}/100</b> ({fng.get('classification', 'Neutral')})"
            )

        async def _external_global() -> dict[str, Any] | None:
            body = await http_get_json(f"{COINGECKO}/global", timeout=12)
            if body is None:
                return None
            data = body.get("data", {})
            return {
                "total_market_cap": data.get("total_market_cap", {}).get("usd"),
                "btc_dominance": data.get("market_cap_percentage", {}).get("btc"),
            }

        global_data = await resolve_market_global(_external_global)
        if global_data:
            mcap = global_data.get("total_market_cap")
            if mcap:
                lines.append(f"\U0001f4c8 Total Market Cap: <b>{fmt_number(mcap)}</b>")
            btc_dom = global_data.get("btc_dominance")
            if btc_dom:
                lines.append(f"\U0001f4bf BTC Dominance: <b>{btc_dom:.1f}%</b>")

        lines.append(footer_links())
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f504 Refresh", callback_data="sentiment_refresh")],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            ),
            disable_web_page_preview=True,
        )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"inline sentiment failed: {e}")
        await query.edit_message_text(
            "\u274c Sentiment unavailable. Please try again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
