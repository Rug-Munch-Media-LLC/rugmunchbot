"""market — command handlers + registration (split from handlers/market.py, 2026-09-18)."""

from __future__ import annotations

import asyncio
import contextlib
import html
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CommandHandler

from rugmunchbot import db
from rugmunchbot.config import (
    BACKEND_URL,
    TELEGRAM_CHANNEL,
    WEBSITE_URL,
    backend_headers,
    fmt_chain,
    fmt_pct,
)
from rugmunchbot.formatters import (
    footer_links,
    is_owner,
    paywall_text,
    sep,
    thin_sep,
)
from rugmunchbot.handlers._market_helpers import (
    _resolve_chain,
    _time_ago as _time_ago,
    _window_change,
)
from rugmunchbot.handlers._trending import _fetch_cg_trending_news, _fetch_dexscreener_trending_news
from rugmunchbot.keyboards import back_kb, token_buttons
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json

if TYPE_CHECKING:
    from telegram.ext import Application, ContextTypes

from ._data import _fetch_market_intel
from ._render import (
    _MOVERS_TF_LABEL,
    _change_emoji,
    _fmt_usd,
    _render_movers_lines,
    _whale_analysis_bits,
    _whale_flow_summary,
    render_trending,
)

logger = get_logger("rmi_bot")


MARKET_UNAVAILABLE = (
    "\u26a0\ufe0f <b>Market Data Unavailable</b>\n\n"
    "Market data is temporarily unavailable.\nPlease try again in a moment."
)


_FREE_WHALE_MOVES = 3  # free tier sees the top 3; premium unlocks the full 10


async def cmd_rugcheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick rug pull checklist - educational."""
    from telegram.constants import ParseMode

    from rugmunchbot.formatters import footer_links, sep

    text = (
        f"\U0001f6a9 <b>Rug Pull Checklist</b>\n{sep()}\n\n"
        f"Before buying ANY token, check these:\n\n"
        f"<b>\U0001f534 RED FLAGS (Run!)</b>\n{thin_sep()}\n"
        f"  \u274c Honeypot detected (can't sell)\n"
        f"  \u274c Unrenounced ownership\n"
        f"  \u274c Mintable supply (infinite tokens)\n"
        f"  \u274c Blacklist function exists\n"
        f"  \u274c LP not locked (< 50%)\n"
        f"  \u274c Buy/sell tax > 10%\n\n"
        f"<b>\U0001f7e1 YELLOW FLAGS (Caution)</b>\n{thin_sep()}\n"
        f"  \u26a0\ufe0f Bundle activity detected\n"
        f"  \u26a0\ufe0f >50% fresh wallets (<7 days)\n"
        f"  \u26a0\ufe0f Dev wallet holds >20%\n"
        f"  \u26a0\ufe0f Suspicious volume/mcap ratio\n"
        f"  \u26a0\ufe0f Very low liquidity vs mcap\n\n"
        f"<b>\U0001f7e2 GREEN FLAGS (Safer)</b>\n{thin_sep()}\n"
        f"  \u2705 Contract verified\n"
        f"  \u2705 Ownership renounced\n"
        f"  \u2705 LP locked > 90%\n"
        f"  \u2705 No blacklist/mint functions\n"
        f"  \u2705 Diverse holder distribution\n"
        f"  \u2705 Organic volume patterns\n\n"
        f"<b>\U0001f6e1\ufe0f Always run /scan before buying!</b>"
        f"{footer_links()}"
    )
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /trending [chain] — hot tokens from fused multi-source data."""
    chain = _resolve_chain(ctx.args[0]) if ctx.args else "all"
    msg = await update.message.reply_text(
        "🔥 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    text, trending_kb = await render_trending(chain, limit=20, window="24h")
    if text is None or trending_kb is None:
        await msg.edit_text(
            "❌ <b>Trending Unavailable</b>\n\nCould not fetch trending tokens right now.\nPlease try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
        return
    await msg.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=trending_kb,
        disable_web_page_preview=True,
    )


def _menu_kb() -> InlineKeyboardMarkup:
    """Shared bottom bar: back to the main menu."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]
    )


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /market — global market overview (mcap, volume, dominance, fear/greed, BTC/ETH)."""
    msg = await update.message.reply_text(
        "\U0001f4ca <b>Fetching market overview...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    overview, macro = await asyncio.gather(
        _fetch_market_intel("/api/v1/market/overview"),
        _fetch_market_intel("/api/v1/market/macro"),
    )
    if not overview:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f4ca <b>Market Overview</b>", sep()]
    if overview.get("total_market_cap") is not None:
        lines.append(
            f"\U0001f310 Total Market Cap: <b>{_fmt_usd(overview.get('total_market_cap'))}</b>"
        )
    if overview.get("total_volume_24h") is not None:
        lines.append(f"\U0001f4c8 24h Volume: <b>{_fmt_usd(overview.get('total_volume_24h'))}</b>")
    if overview.get("btc_dominance") is not None:
        lines.append(f"\u20bf BTC Dominance: <b>{float(overview['btc_dominance']):.1f}%</b>")
    if overview.get("eth_dominance") is not None:
        lines.append(f"\u27e0 ETH Dominance: <b>{float(overview['eth_dominance']):.1f}%</b>")
    active = overview.get("active_cryptocurrencies")
    if active:
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
            lines.append(
                f"\U0001f4b0 <b>{asset}:</b> {_fmt_usd(it.get('current_price'))}{change_txt}"
            )

    lines.append(footer_links())
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_kb(),
        disable_web_page_preview=True,
    )


async def cmd_movers(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /movers [1h|6h|24h|7d] — top gainers/losers (real degen movers)."""
    timeframe = "24h"
    if ctx.args and ctx.args[0].lower() in _MOVERS_TF_LABEL:
        timeframe = ctx.args[0].lower()
    label = _MOVERS_TF_LABEL.get(timeframe, "24H")
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f680 <b>Fetching {label} market movers...</b>", parse_mode=ParseMode.HTML
    )
    data = await _fetch_market_intel(
        "/api/v1/market/movers", params={"limit": 20, "timeframe": timeframe}
    )
    if not data:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    # Backend returns explicit lists for the requested window; fall back to
    # splitting the raw items when only "items" is present (older backends).
    gainers = list((data.get("top_gainers") or [])[:6])
    losers = list((data.get("top_losers") or [])[:6])
    if not gainers and not losers:
        items = (data or {}).get("items") or []
        gainers = [i for i in items if _window_change(i, timeframe) >= 0][:6]
        losers = [i for i in items if _window_change(i, timeframe) < 0][:6]

    lines = _render_movers_lines(gainers, losers, timeframe, data.get("sources"))

    rows: list[list[InlineKeyboardButton]] = []
    for item in (gainers + losers)[:8]:
        addr = item.get("address") or ""
        if not addr:
            continue
        chain = (item.get("chain") or "solana").lower()
        if (item.get("source") or "").lower() == "gmgn":
            rows.append(token_buttons(addr, chain))
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        "\U0001f4c8 Chart", url=f"https://www.coingecko.com/en/coins/{addr}"
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


async def cmd_launches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /launches — new token launches from the backend market router."""
    msg = await update.message.reply_text(
        "\U0001f195 <b>Fetching new launches...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    data = await _fetch_market_intel("/api/v1/market/launches", params={"limit": 8})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f195 <b>New Token Launches</b>", sep()]
    for i, item in enumerate(items[:8], 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:28]
        change = item.get("change_24h") or 0
        chain_txt = fmt_chain(item.get("chain") or "solana")
        bundler = "\U0001f6a8 bundler" if item.get("bundler_detected") else ""
        addr = item.get("address") or ""
        lines.append(
            f"{i}. {_change_emoji(change)} <b>{name}</b> ({symbol}) {chain_txt}\n"
            f"   {_fmt_usd(item.get('price'))} | <b>{fmt_pct(change)}</b> | Vol: {_fmt_usd(item.get('volume_24h'))}"
            f"{f' | {bundler}' if bundler else ''}"
        )
        if addr:
            lines.append(f"   <code>{addr}</code>")
    lines.append("\n<i>Fresh launches \u2014 always /scan before buying.</i>")
    lines.append(footer_links())
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:8]:
        addr = item.get("address") or ""
        if addr:
            rows.append(token_buttons(addr, item.get("chain") or "solana"))
    rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data="launches_refresh")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


async def cmd_whales(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /whales — deep whale analysis: top moves, funding sources,
    wallet ages, historical patterns, net-flow summary and confidence.

    [REVIEW 2026-09-07] Backend also exposes per-token
    ``GET /api/v1/whales/flow`` and ``GET /api/v1/whales/signals`` (both
    require ``address`` + ``chain`` and return a SINGLE token's net-flow /
    rendered signal). This command renders a MARKET-WIDE list of top whale
    moves (``data["items"]``), so those per-token endpoints do NOT cover this
    shape — switching here would break the feed. They are the right source
    for a per-token whale panel (e.g. a scan/token-detail view), not for the
    market-wide /whales list. Left on /api/v1/market/whales.
    """
    msg = await update.message.reply_text(
        "\U0001f40b <b>Deep-scanning whale flow (24h)...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    data = await _fetch_market_intel("/api/v1/market/whales", params={"limit": 10})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    user = update.effective_user
    tier = db.get_user_tier(user.id) if user else "free"
    premium = tier in ("scout", "hunter", "pro") or (user is not None and is_owner(user.id))

    summary = _whale_flow_summary(items)
    shown = items if premium else items[:_FREE_WHALE_MOVES]

    lines = ["\U0001f40b <b>Whale Flow \u2014 Deep Analysis (24h)</b>", sep()]
    lines.append("<b>\U0001f4ca Whale Summary</b>")
    lines.append(
        f"   \U0001f7e2 Buy volume: <b>{_fmt_usd(summary['buy_vol'])}</b>"
        f" ({summary['buys']} moves) \u00b7 \U0001f534 Sell volume: <b>{_fmt_usd(summary['sell_vol'])}</b>"
        f" ({summary['sells']} moves)"
    )
    net = summary["net"]
    net_txt = f"+{_fmt_usd(net)}" if net >= 0 else f"-{_fmt_usd(abs(net))}"
    ratio = summary["ratio"]
    ratio_txt = f"{ratio:.2f}:1" if ratio is not None else "all buys"
    lines.append(
        f"   \U0001f4b0 Net flow: <b>{net_txt}</b> \u00b7 Buy/sell ratio: <b>{ratio_txt}</b>"
    )
    lines.append(f"   \U0001f3af Whale Confidence: <b>{summary['verdict']}</b>")
    lines.append("")

    lines.append(f"<b>\U0001f40b Top Whale Moves</b> ({len(shown)}/{len(items)})")
    lines.append(thin_sep())
    for i, item in enumerate(shown, 1):
        symbol = (item.get("token_symbol") or "???").upper()[:12]
        action = (item.get("type") or "buy").lower()
        action_txt = "BUY" if action == "buy" else "SELL"
        action_emoji = "\U0001f7e2" if action == "buy" else "\U0001f534"
        chain_raw = item.get("chain") or ""
        chain_txt = f" {fmt_chain(chain_raw)}" if chain_raw else ""
        addr = item.get("token_address") or ""
        wallet = (
            item.get("wallet") or item.get("from") or item.get("to") or item.get("exchange") or ""
        )
        amount = item.get("amount")
        amount_txt = f" \u00b7 {amount}" if amount else ""
        wallet_txt = f" | \U0001f3e6 {html.escape(str(wallet)[:24])}" if wallet else ""
        lines.append(
            f"{i}. {action_emoji} <b>{action_txt} {symbol}</b>{chain_txt}\n"
            f"   <b>{_fmt_usd(item.get('value_usd'))}</b>{amount_txt}"
            f"{wallet_txt}"
            f"{f' | <code>{addr}</code>' if addr else ''}"
        )
        if premium:
            bits = _whale_analysis_bits(item)
            if bits:
                joined = " \u00b7 ".join(bits)
                lines.append(f"   \U0001f9e0 {joined}")
        if i < len(shown):
            lines.append("")

    if not premium:
        locked = len(items) - _FREE_WHALE_MOVES
        lines.append("")
        lines.append(sep())
        lines.append(
            f"\U0001f512 <b>{locked} more whale moves locked</b> \u2014 plus funding sources, "
            "wallet ages and accumulator/dumper history for every whale."
        )
        lines.append(paywall_text(user.id, "whales") if user else paywall_text(0, "scan"))

    lines.append(footer_links())
    rows: list[list[InlineKeyboardButton]] = []
    for item in shown[:5]:
        addr = item.get("token_address") or ""
        if addr:
            rows.append(token_buttons(addr, item.get("chain") or "solana"))
    if not premium:
        rows.append(
            [
                InlineKeyboardButton(
                    "\U0001f48e Unlock Full Whale Analysis", callback_data="menu_pricing"
                )
            ]
        )
    rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data="whales_refresh")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


def register_market(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register market-awareness commands on the bot application.

    Wires /market, /trending, /movers, /launches and /whales in one call.
    The Lead wires this from bot.py; replace the old per-command registrations
    (e.g. ``_reg(app, "trending", cmd_trending)``) with this call so commands
    are not double-registered.
    """
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("movers", cmd_movers))
    app.add_handler(CommandHandler("launches", cmd_launches))


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

    news_kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")],
            [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
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
