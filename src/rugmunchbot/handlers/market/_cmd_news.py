"""market /news — internal feed, backend trending, CoinGecko and DexScreener fallbacks."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError

from rugmunchbot.config import BACKEND_URL, TELEGRAM_CHANNEL, WEBSITE_URL, backend_headers
from rugmunchbot.formatters import footer_links, sep
from rugmunchbot.handlers._market_helpers import _time_ago
from rugmunchbot.handlers._site_links import with_site_link
from rugmunchbot.handlers._trending import (
    _fetch_cg_trending_news,
    _fetch_dexscreener_trending_news,
)
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch and render the latest news (internal feed -> backend -> CoinGecko fallback)."""
    msg = await update.message.reply_text(
        "\U0001f4f0 <b>Fetching news...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    articles = []
    source_label = "RMI News Network"

    # Try the internal news-feed endpoint first (has bias/sentiment/factual ratings)
    try:
        body = await http_get_json(
            f"{BACKEND_URL}/api/v1/internal/news-feed",
            params={"channel": "news", "limit": 5, "min_score": 0},
            headers=backend_headers(),
            timeout=10,
        )
        if body:
            articles = body.get("items", [])
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("cmd_news internal feed failed: %s", e)

    # Fallback to backend trending news
    if not articles:
        source_label = "Trending"
        try:
            body = await http_get_json(
                f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5",
                headers=backend_headers(),
                timeout=10,
            )
            if body:
                articles = body.get("items", [])
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
        ) as e:
            logger.warning("cmd_news backend fetch failed: %s", e)

    # Fallback to CoinGecko trending
    if not articles:
        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "CoinGecko Trending"

    # Final fallback: use DexScreener trending
    if not articles:
        ds_items = await _fetch_dexscreener_trending_news()
        if ds_items:
            articles = ds_items
            source_label = "DexScreener Trending"

    news_kb = with_site_link(
        "news",
        InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
                [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        ),
    )

    if articles:
        bias_emoji = {
            "left": "\U0001f535",
            "center-left": "\U0001f537",
            "center": "\u26aa",
            "center-right": "\U0001f536",
            "right": "\U0001f534",
        }
        lines = ["\U0001f4f0 <b>Crypto News</b>", f"<i>via {source_label}</i>", sep()]
        for a in articles[:5]:
            title = (a.get("title") or "Untitled")[:100]
            source = a.get("source", "News")
            published = _time_ago(a.get("published_at") or a.get("publishedAt"))
            pub_str = f" \u00b7 {published}" if published else ""
            url = a.get("url") or a.get("link") or ""
            title_linked = (
                f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'
                if url
                else html.escape(title)
            )

            # Detail line for trending tokens
            detail = a.get("detail", "")
            if detail:
                lines.append(
                    f"📰 {title_linked}\n  <i>{html.escape(source)}</i>{pub_str}\n  {detail}"
                )
                continue

            # Bias/sentiment/factual ratings
            bias = a.get("bias", "center")
            sentiment = a.get("sentiment_score", 0)
            factual = a.get("factual_rating", "N/A")
            source_tier = a.get("source_tier", 5)
            sent_emoji = (
                "\U0001f525" if sentiment > 30 else "\U0001f9ca" if sentiment < -30 else "\u2795"
            )
            bias_e = bias_emoji.get(bias, "\u26aa")
            tier_str = (
                "\u2605" * max(1, 6 - int(source_tier))
                if isinstance(source_tier, (int, float))
                else ""
            )

            lines.append(
                f"{sent_emoji} {bias_e} [{tier_str}] {title_linked}\n"
                f"  <i>{html.escape(source)}</i>{pub_str} \u00b7 Factuality: {factual}"
            )
        lines.append(footer_links())
        text = "\n".join(lines)
    else:
        text = (
            f"\U0001f4f0 <b>Crypto News</b>\n\n"
            f"<i>News feed temporarily unavailable.</i>\n\n"
            f'\U0001f4e2 Follow <a href="{TELEGRAM_CHANNEL}">@rmialerts</a> for real-time alerts!\n'
            f'\U0001f310 <a href="{WEBSITE_URL}">rugmunch.io</a>'
        )

    try:
        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=news_kb, disable_web_page_preview=True
        )
    except TelegramError:  # message too old / not modified / chat deleted
        logger.debug("news:edit_failed", exc_info=True)
