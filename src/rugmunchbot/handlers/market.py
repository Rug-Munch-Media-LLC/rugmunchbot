#!/usr/bin/env python3
"""
RugMunchBot -- Market data command handlers.

Commands: trending, news + helpers: _time_ago, _fetch_cg_trending_news
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    TELEGRAM_CHANNEL,
    WEBSITE_URL,
    backend_headers,
    fmt_chain,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    dexscreener_url,
    footer_links,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.handlers._market_helpers import (
    _build_trending_kb,
    _render_trending_text,
    _resolve_chain,
    _time_ago as _time_ago,  # re-exported — handlers/callback imports from market
    _window_change,
)
from app.domains.telegram.rugmunchbot.handlers._trending import (
    _fetch_cg_trending_news,
    _fetch_fused_trending,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, token_buttons
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import (
    http_get_json,
)

if TYPE_CHECKING:
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot")

__all__ = [
    "MARKET_UNAVAILABLE",
    "_TRENDING_KEY_PREFIX",
    "_TRENDING_TTL",
    "_cache_get_trending",
    "_cache_set_trending",
    "_change_emoji",
    "_fetch_cg_trending_news",
    "_fetch_fused_trending",
    "_fetch_market_intel",
    "_fmt_usd",
    "_mover_link",
    "_redis_client",
    "_render_movers_lines",
    "_time_ago",
    "cmd_launches",
    "cmd_market",
    "cmd_movers",
    "cmd_news",
    "cmd_rugcheck",
    "cmd_trending",
    "cmd_whales",
    "register_market",
    "render_trending",
]


async def cmd_rugcheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick rug pull checklist - educational."""
    from telegram.constants import ParseMode

    from app.domains.telegram.rugmunchbot.formatters import footer_links, sep

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


# ══════════════════════════════════════════════
# /trending Redis cache — fused token list, TTL 60-120s
# The fused trending pipeline fans out to RugCharts + DexScreener boosts +
# DexScreener fresh-launch profiles and then enriches each token with a
# backend quick scan (up to ~20 external calls). The cache serves the SAME
# fused token list for the TTL window so repeated /trending taps (and the
# inline callback paths, which call ``render_trending`` with the same key)
# do not re-hammer the sources. Fail-open: Redis down → live fetch every
# time.
# ══════════════════════════════════════════════
_TRENDING_TTL = 90  # within the 60-120s requirement
_TRENDING_KEY_PREFIX = "trending:"  # spec key: trending:{chain}:{window}


def _redis_client() -> Any:
    """Async Redis client (lazy import — fail-open when unavailable)."""
    from app.core.redis import get_redis_async

    return get_redis_async()


async def _cache_get_trending(chain: str, window: str) -> list[dict[str, Any]] | None:
    """Load the cached fused trending token list (None on any miss)."""
    r = _redis_client()
    if r is None:
        return None
    try:
        raw = await r.get(f"{_TRENDING_KEY_PREFIX}{chain}:{window}")
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, list) else None
    except Exception:  # fail-open by contract
        logger.debug("trending:cache_get_failed chain=%s window=%s", chain, window, exc_info=True)
        return None


async def _cache_set_trending(chain: str, window: str, tokens: list[dict[str, Any]]) -> None:
    """Write the fused trending token list to Redis. Fail-open."""
    r = _redis_client()
    if r is None:
        return
    try:
        await r.set(
            f"{_TRENDING_KEY_PREFIX}{chain}:{window}",
            json.dumps(tokens, default=str),
            ex=_TRENDING_TTL,
        )
    except Exception:  # fail-open by contract
        logger.debug("trending:cache_set_failed chain=%s window=%s", chain, window, exc_info=True)


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
        return _render_trending_text(cached_tokens, chain, window), _build_trending_kb(cached_tokens, chain, window)

    tokens = await _fetch_fused_trending(chain, limit)
    if not tokens:
        return None, None
    await _cache_set_trending(chain, window, tokens)
    return _render_trending_text(tokens, chain, window), _build_trending_kb(tokens, chain, window)


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /trending [chain] — hot tokens from fused multi-source data."""
    chain = _resolve_chain(ctx.args[0]) if ctx.args else "solana"
    msg = await update.message.reply_text("🔥 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
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


# ══════════════════════════════════════════════
# MARKET INTELLIGENCE (/market /movers /launches /whales)
# Backed by the backend market-intel router (app/routers/market_intel).
# The backend caches every endpoint (CoinGecko/DexScreener TTL) — the bot
# does NOT add its own cache layer.
# ══════════════════════════════════════════════

MARKET_UNAVAILABLE = (
    "\u26a0\ufe0f <b>Market Data Unavailable</b>\n\n"
    "Market data is temporarily unavailable.\nPlease try again in a moment."
)


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


async def _fetch_market_intel(
    path: str, params: dict[str, Any] | None = None, timeout_secs: float = 10
) -> dict[str, Any] | None:
    """GET a backend market-intel endpoint with the shared-secret header.

    Returns the parsed JSON body or None on any failure (network, non-200,
    malformed body). Callers render a graceful unavailable state on None.
    """
    try:
        body = await http_get_json(
            f"{BACKEND_URL}{path}",
            params=params,
            headers=backend_headers(),
            timeout=timeout_secs,
        )
        if body is None:
            logger.debug("market intel %s status non-200", path)
        return body
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("market intel %s fetch failed: %s", path, exc)
        return None


def _menu_kb() -> InlineKeyboardMarkup:
    """Shared bottom bar: back to the main menu."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]])


def _change_emoji(change: float) -> str:
    return "\U0001f7e2" if change >= 0 else "\U0001f534"


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /market — global market overview (mcap, volume, dominance, fear/greed, BTC/ETH)."""
    msg = await update.message.reply_text("\U0001f4ca <b>Fetching market overview...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    overview, macro = await asyncio.gather(
        _fetch_market_intel("/api/v1/market/overview"),
        _fetch_market_intel("/api/v1/market/macro"),
    )
    if not overview:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f4ca <b>Market Overview</b>", sep()]
    if overview.get("total_market_cap") is not None:
        lines.append(f"\U0001f310 Total Market Cap: <b>{_fmt_usd(overview.get('total_market_cap'))}</b>")
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
            lines.append(f"\U0001f4b0 <b>{asset}:</b> {_fmt_usd(it.get('current_price'))}{change_txt}")

    lines.append(footer_links())
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_kb(),
        disable_web_page_preview=True,
    )


_MOVERS_TF_LABEL = {"1h": "1H", "6h": "6H", "24h": "24H", "7d": "7D"}


def _mover_link(item: dict[str, Any]) -> str:
    """Tap-through URL for a mover: DexScreener for on-chain tokens,
    CoinGecko coin page for exchange-listed (CG) entries."""
    addr = item.get("address") or ""
    chain = (item.get("chain") or "solana").lower()
    if item.get("source") == "gmgn" and addr:
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
    if not losers:
        lines.append("<i>No losers in the top movers \u2014 market is green \U0001f7e2</i>")
    lines.append(footer_links())
    return lines


async def cmd_movers(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /movers [1h|6h|24h|7d] — top gainers/losers (real degen movers)."""
    timeframe = "24h"
    if ctx.args and ctx.args[0].lower() in _MOVERS_TF_LABEL:
        timeframe = ctx.args[0].lower()
    label = _MOVERS_TF_LABEL.get(timeframe, "24H")
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f680 <b>Fetching {label} market movers...</b>", parse_mode=ParseMode.HTML
    )
    data = await _fetch_market_intel("/api/v1/market/movers", params={"limit": 20, "timeframe": timeframe})
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
            rows.append([InlineKeyboardButton("\U0001f4c8 Chart", url=f"https://www.coingecko.com/en/coins/{addr}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


async def cmd_launches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /launches — new token launches from the backend market router."""
    msg = await update.message.reply_text("\U0001f195 <b>Fetching new launches...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
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
    """Handle /whales — recent whale moves from the backend market router."""
    msg = await update.message.reply_text("\U0001f40b <b>Fetching whale activity...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    data = await _fetch_market_intel("/api/v1/market/whales", params={"limit": 5})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

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
            f"   {_fmt_usd(item.get('value_usd'))}"
            f"{f' | <code>{addr}</code>' if addr else ''}"
        )
    lines.append("\n<i>Large moves detected across Solana/Ethereum/Bitcoin pairs.</i>")
    lines.append(footer_links())
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:5]:
        addr = item.get("token_address") or ""
        if addr:
            rows.append(token_buttons(addr, item.get("chain") or "solana"))
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
    app.add_handler(CommandHandler("whales", cmd_whales))


async def cmd_news(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch and render the latest news (internal feed -> backend -> CoinGecko fallback)."""
    msg = await update.message.reply_text("\U0001f4f0 <b>Fetching news...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
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
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning("cmd_news backend fetch failed: %s", e)

    if not articles:
        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "Trending"

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
        lines = ["\U0001f4f0 <b>Rated Crypto News</b>", f"<i>via {source_label}</i>", sep()]
        for a in articles[:5]:
            title = (a.get("title") or "Untitled")[:100]
            source = a.get("source", "News")
            published = _time_ago(a.get("published_at") or a.get("publishedAt"))
            pub_str = f" \u00b7 {published}" if published else ""
            url = a.get("url") or a.get("link") or ""
            title_linked = (
                f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>' if url else html.escape(title)
            )

            # Bias/sentiment/factual ratings
            bias = a.get("bias", "center")
            sentiment = a.get("sentiment_score", 0)
            factual = a.get("factual_rating", "N/A")
            source_tier = a.get("source_tier", 5)
            sent_emoji = "\U0001f525" if sentiment > 30 else "\U0001f9ca" if sentiment < -30 else "\u2795"
            bias_e = bias_emoji.get(bias, "\u26aa")
            tier_str = "\u2605" * max(1, 6 - int(source_tier)) if isinstance(source_tier, (int, float)) else ""

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

    with contextlib.suppress(Exception):
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=news_kb,
            disable_web_page_preview=True,
        )
