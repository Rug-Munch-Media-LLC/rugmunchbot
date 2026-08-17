#!/usr/bin/env python3
"""
RugMunchBot -- callback refresh branches.

Trending / news / sentiment / market refresh callbacks. Split out of the
historic single-function ``handle_callback`` chain (2026-08-16).

Exact matches are checked before their ``startswith`` twins (``news_refresh``
and ``trending_refresh``), exactly as in the original chain. The
``sentiment_refresh`` branch is shadowed by ``_actions``' generic
``sentiment_`` branch (a two-part payload does nothing there), matching the
original if/elif behavior — kept for structural fidelity.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, TELEGRAM_CHANNEL, backend_headers
from app.domains.telegram.rugmunchbot.formatters import footer_links, sep
from app.domains.telegram.rugmunchbot.handlers.callback._inline import _inline_market_data
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def _render_trending_reply(query: Any, chain: str, window: str) -> None:
    """Fetch and render trending, with fail-open error card."""
    from app.domains.telegram.rugmunchbot.handlers.market import render_trending

    trend_text, trend_kb = await render_trending(chain, limit=8, window=window)
    if trend_text is None or trend_kb is None:
        await query.edit_message_text(
            "\u274c Could not fetch trending data.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    await query.edit_message_text(
        trend_text,
        parse_mode=ParseMode.HTML,
        reply_markup=trend_kb,
        disable_web_page_preview=True,
    )


async def handle(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    query: Any,
    data: str,
) -> bool:
    """Handle refresh/reload callbacks. True when a branch matched."""
    if data == "trending_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("\U0001f504 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        await _render_trending_reply(query, "solana", "24h")
    elif data == "news_refresh":
        await query.answer("Refreshing...")
        await query.edit_message_text("\U0001f4f0 <b>Refreshing news...</b>", parse_mode=ParseMode.HTML)
        articles = []
        source_label = "Backend"
        body = await http_get_json(
            f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5",
            headers=backend_headers(),
            timeout=10,
        )
        if body:
            articles = body.get("items", [])
        else:
            logger.warning("news_refresh backend failed")
        if not articles:
            from app.domains.telegram.rugmunchbot.handlers.market import (
                _fetch_cg_trending_news,
                _time_ago,
            )

            cg_items = await _fetch_cg_trending_news()
            if cg_items:
                articles = cg_items
                source_label = "Trending"
        if articles:
            lines = ["\U0001f4f0 <b>Crypto News</b>", f"<i>via {source_label}</i>", sep()]
            for a in articles[:5]:
                title = (a.get("title") or "Untitled")[:100]
                source = a.get("source", "News")
                published = _time_ago(a.get("published_at") or a.get("publishedAt"))
                pub_str = f" \u00b7 {published}" if published else ""
                url = a.get("url") or a.get("link") or ""
                title_linked = f'<a href="{url}">{title}</a>' if url else title
                lines.append(f"\U0001f4f0 {title_linked}\n  <i>{source}</i>{pub_str}")
            lines.append(footer_links())
            news_kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
                    [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            )
            with contextlib.suppress(Exception):
                await query.edit_message_text(
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                    reply_markup=news_kb,
                    disable_web_page_preview=True,
                )
        else:
            await query.edit_message_text(
                "\U0001f4f0 <b>Crypto News</b>\n\nNews feed temporarily unavailable.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
    elif data.startswith("news_refresh"):
        await query.edit_message_text("\U0001f4f0 <b>Refreshing news...</b>", parse_mode=ParseMode.HTML)
        # Import and re-run cmd_news
        from app.domains.telegram.rugmunchbot.handlers.market import cmd_news as _news

        ctx.args = []
        await _news(update, ctx)
    elif data.startswith("sentiment_refresh"):
        # Shadowed by the earlier ``sentiment_`` branch (see module docstring);
        # kept for structural fidelity with the original chain.
        await query.edit_message_text("\U0001f4c8 <b>Refreshing sentiment...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.sentiment import cmd_sentiment as _sentiment

        ctx.args = []
        await _sentiment(update, ctx)
    elif data.startswith("trending_chain_"):
        # Switch chain: trending_chain_{chain}
        new_chain = data[len("trending_chain_") :]
        if new_chain in ("solana", "ethereum", "base", "bsc", "arbitrum"):
            await query.edit_message_text(
                f"\U0001f525 <b>Loading {new_chain.capitalize()} trending...</b>",
                parse_mode=ParseMode.HTML,
            )
            await _render_trending_reply(query, new_chain, "24h")
        else:
            await query.answer("Chain not supported.", show_alert=True)
    elif data.startswith("trending_win_"):
        parts = data.split("_", 3)
        if len(parts) == 4 and parts[3] in ("1h", "24h", "7d"):
            win_chain, win = parts[2], parts[3]
            await query.edit_message_text("\U0001f525 <b>Loading trending...</b>", parse_mode=ParseMode.HTML)
            await _render_trending_reply(query, win_chain, win)
        else:
            await query.answer("Invalid request.", show_alert=True)
    elif data.startswith("trending_refresh"):
        await query.edit_message_text("\U0001f525 <b>Refreshing trending...</b>", parse_mode=ParseMode.HTML)
        from app.domains.telegram.rugmunchbot.handlers.market import cmd_trending as _trending

        parts = data.split("_", 2)
        ctx.args = [parts[2]] if len(parts) == 3 and parts[2] else []
        await _trending(update, ctx)
    elif data.startswith("market_refresh"):
        await _inline_market_data(update, ctx, "market")
    elif data.startswith("movers_refresh"):
        await _inline_market_data(update, ctx, "movers")
    elif data.startswith("launches_refresh"):
        await _inline_market_data(update, ctx, "launches")
    elif data.startswith("whales_refresh"):
        await _inline_market_data(update, ctx, "whales")
    else:
        return False
    return True
