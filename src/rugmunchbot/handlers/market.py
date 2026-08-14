#!/usr/bin/env python3
"""
RugMunchBot -- Market data command handlers.

Commands: trending, news + helpers: _time_ago, _fetch_cg_trending_news

Ported from the private monorepo (rmi-backend rugmunchbot) — import-rewritten
to ``rugmunchbot.*`` per docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md §2.3.
Branding scrub per §6.15: the live bot's hardcoded alerts handle + website
anchor text in ``cmd_news`` are replaced with env-driven neutral labels; the
"Alerts Channel" button only renders when ``BOT_TELEGRAM_CHANNEL`` is set. [REVIEW]
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    TELEGRAM_CHANNEL,
    WEBSITE_URL,
    backend_headers,
    fmt_chain,
    fmt_pct,
)
from rugmunchbot.formatters import (
    dexscreener_url,
    footer_links,
    sep,
    thin_sep,
)
from rugmunchbot.handlers._market_helpers import (
    _build_trending_kb,
    _merge_trending_sources,
    _render_trending_text,
    _resolve_chain,
    _time_ago as _time_ago,  # re-exported — handlers/callback imports from market
    _window_change,
)
from rugmunchbot.keyboards import back_kb, token_buttons
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot")


async def _fetch_cg_trending_news() -> list[dict[str, Any]] | None:
    """Fallback: fetch CoinGecko trending coins as pseudo-news items."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.coingecko.com/api/v3/search/trending")
            if r.status_code != 200:
                return None
            coins = r.json().get("coins", [])
            if not coins:
                return None
            items = []
            for c in coins[:5]:
                item = c.get("item", {})
                items.append(
                    {
                        "title": f"{item.get('name', 'Unknown')} ({item.get('symbol', '?')}) trending now",
                        "source": "Trending",
                        "published_at": None,
                        "url": f"https://www.coingecko.com/en/coins/{item.get('id', '')}",
                        "score": item.get("score", 0) or 0,
                    }
                )
            return items
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("CoinGecko trending fallback failed: %s", exc)
        return None


async def cmd_rugcheck(update, ctx) -> None:
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
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


async def _fetch_dexscreener_trending(
    chain: str = "solana", limit: int = 20
) -> list[dict[str, Any]] | None:
    """Fetch trending from DexScreener token boosts, then enrich with token details."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Step 1: Get boosted tokens
            r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
            if r.status_code != 200:
                return None
            boosted = r.json()
            if not isinstance(boosted, list):
                return None

            # Step 2: Fetch token details for each boosted token (parallel, max 10)
            semaphore = asyncio.Semaphore(10)

            async def fetch_token_details(item: dict[str, Any]) -> dict[str, Any] | None:
                if not isinstance(item, dict):
                    return None
                token_addr = item.get("tokenAddress") or item.get("address")
                if not token_addr:
                    return None
                # Filter by chain if specified
                item_chain = item.get("chainId", "").lower()
                if chain and chain != "all" and item_chain != chain.lower():
                    return None

                async with semaphore:
                    try:
                        r = await client.get(
                            f"https://api.dexscreener.com/latest/dex/tokens/{token_addr}", timeout=8
                        )
                        if r.status_code != 200:
                            return None
                        data = r.json()
                        pairs = data.get("pairs", [])
                        if not pairs:
                            return None
                        p = pairs[0]
                        bt = p.get("baseToken", {})
                        name = bt.get("name") or "Unknown"
                        symbol = bt.get("symbol") or "???"
                        # Skip obvious garbage and low-quality tokens
                        garbage_patterns = [
                            "crawled",
                            "smol",
                            "anaem",
                            "dev leave",
                            "robinhood",
                            "mow",
                            "pengu mow",  # known low-quality meme tokens
                        ]
                        if any(g in name.lower() for g in garbage_patterns):
                            return None
                        if any(g in symbol.lower() for g in garbage_patterns):
                            return None
                        # Minimum quality thresholds
                        liquidity = float(p.get("liquidity", {}).get("usd") or 0)
                        volume_24h = float(p.get("volume", {}).get("h24") or 0)
                        # Skip tokens with very low liquidity (<$1000) or volume (<$5000)
                        if liquidity < 1000 or volume_24h < 5000:
                            return None
                        return {
                            "address": token_addr,
                            "chain": p.get("chainId", chain),
                            "name": name,
                            "symbol": symbol,
                            "price_usd": float(p.get("priceUsd") or 0),
                            "volume_24h": float(p.get("volume", {}).get("h24") or 0),
                            "liquidity_usd": float(p.get("liquidity", {}).get("usd") or 0),
                            "change_24h": float(p.get("priceChange", {}).get("h24") or 0),
                            "change_1h": float(p.get("priceChange", {}).get("h1") or 0),
                            "change_7d": float(p.get("priceChange", {}).get("d7") or 0),
                            "created_at": p.get("pairCreatedAt"),
                            "dex": p.get("dexId"),
                            "info": p.get("info", {}),
                            "source": "dexscreener",
                        }
                    except Exception:
                        return None

            # Fetch details for top boosted tokens
            tasks = [fetch_token_details(item) for item in boosted[: limit * 2]]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            tokens = [r for r in results if isinstance(r, dict) and r is not None]
            return tokens[:limit] if tokens else None

    except Exception as exc:
        logger.debug("dexscreener trending fetch failed: %s", exc)
        return None


async def _fetch_coingecko_trending(
    chain: str = "solana", limit: int = 15
) -> list[dict[str, Any]] | None:
    """Fetch trending from CoinGecko."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.coingecko.com/api/v3/search/trending")
            if r.status_code != 200:
                return None
            data = r.json()
            coins = data.get("coins", [])
            if not coins:
                return None
            tokens = []
            for c in coins[:limit]:
                item = c.get("item", {})
                if not item:
                    continue
                # CoinGecko doesn't give contract addresses directly
                # We'll use the id for matching later
                tokens.append(
                    {
                        "coingecko_id": item.get("id"),
                        "name": item.get("name") or "Unknown",
                        "symbol": (item.get("symbol") or "???").upper(),
                        "score": item.get("score", 0),
                        "market_cap_rank": item.get("market_cap_rank"),
                        "source": "coingecko",
                    }
                )
            return tokens if tokens else None
    except Exception as exc:
        logger.debug("coingecko trending fetch failed: %s", exc)
        return None


async def _fetch_rugcharts_trending(
    chain: str = "solana", limit: int = 8
) -> list[dict[str, Any]] | None:
    """Fetch trending tokens from the RugCharts backend."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/rugcharts/trending",
                params={"chain": chain, "limit": limit},
                headers=backend_headers(),
            )
            if r.status_code != 200:
                logger.debug("rugcharts trending status %s", r.status_code)
                return None
            body = r.json()
            data = body.get("data") or body
            tokens = data.get("tokens") or []
            for t in tokens:
                t["source"] = "rugcharts"
            return tokens if tokens else None
    except Exception as exc:
        logger.debug("rugcharts trending fetch failed: %s", exc)
        return None


async def _enrich_with_quick_scan(tokens: list[dict[str, Any]], chain: str) -> list[dict[str, Any]]:
    """Add quick risk scan data + DexScreener symbol to trending tokens."""
    if not tokens:
        return tokens

    async def scan_one(token: dict[str, Any]) -> dict[str, Any]:
        addr = token.get("address")
        if not addr:
            return token
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Fetch risk scan
                r = await client.post(
                    f"{BACKEND_URL}/api/v1/bot/scan",
                    headers=backend_headers(),
                    json={"address": addr, "chain": chain},
                )
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, dict) and not data.get("error"):
                        token["risk_score"] = data.get("risk_score")
                        token["holders"] = data.get("holders")
                        token["liquidity_usd"] = data.get("liquidity") or token.get("liquidity_usd")
                        token["bundle_detected"] = data.get("bundle_detected")
                # Fetch symbol from DexScreener if missing
                if not token.get("symbol"):
                    try:
                        r2 = await client.get(f"{DEXSCREENER}/tokens/{addr}", timeout=6)
                        if r2.status_code == 200:
                            pairs = r2.json().get("pairs", [])
                            if pairs:
                                p = pairs[0]
                                token["symbol"] = p.get("baseToken", {}).get("symbol", "")
                                if not token.get("name"):
                                    token["name"] = p.get("baseToken", {}).get("name", "")
                    except Exception:  # noqa: S110 — enrichment is best-effort
                        pass
        except Exception:  # noqa: S110 — return token even if enrichment fails
            pass
        return token

    # Run scans in parallel (max 5 concurrent to avoid overload)
    semaphore = asyncio.Semaphore(5)

    async def limited_scan(t):
        async with semaphore:
            return await scan_one(t)

    enriched = await asyncio.gather(*[limited_scan(t) for t in tokens], return_exceptions=True)
    return [t for t in enriched if isinstance(t, dict)]


async def _fetch_fused_trending(
    chain: str = "solana", limit: int = 20
) -> list[dict[str, Any]] | None:
    """Fetch and fuse trending from all sources with enrichment."""
    # Fetch from all sources in parallel
    rg_task = _fetch_rugcharts_trending(chain, limit)
    ds_task = _fetch_dexscreener_trending(chain, limit)
    cg_task = _fetch_coingecko_trending(chain, limit)

    results = await asyncio.gather(rg_task, ds_task, cg_task, return_exceptions=True)
    rugcharts = results[0] if not isinstance(results[0], BaseException) else None
    dexscreener = results[1] if not isinstance(results[1], BaseException) else None
    coingecko = results[2] if not isinstance(results[2], BaseException) else None

    # Handle exceptions
    if isinstance(rugcharts, Exception):
        rugcharts = None
    if isinstance(dexscreener, Exception):
        dexscreener = None
    if isinstance(coingecko, Exception):
        coingecko = None

    # Merge sources
    fused = _merge_trending_sources(rugcharts, dexscreener, coingecko, limit)

    if not fused:
        return None

    # Enrich with quick risk scans
    enriched = await _enrich_with_quick_scan(fused, chain)

    return enriched


async def render_trending(
    chain: str = "solana", limit: int = 20, window: str = "24h"
) -> tuple[str | None, InlineKeyboardMarkup | None]:
    """Fetch + render fused trending text and its action keyboard."""
    tokens = await _fetch_fused_trending(chain, limit)
    if not tokens:
        return None, None
    return _render_trending_text(tokens, chain, window), _build_trending_kb(tokens, chain, window)


async def _build_trending_text(
    chain: str = "solana", limit: int = 20, window: str = "24h"
) -> str | None:
    """Text-only trending renderer (kept for backward compatibility)."""
    text, _kb = await render_trending(chain, limit, window)
    return text


async def cmd_trending(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /trending [chain] — hot tokens from fused multi-source data."""
    chain = _resolve_chain(ctx.args[0]) if ctx.args else "solana"
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "🔥 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML
    )
    text, trending_kb = await render_trending(chain, limit=20, window="24h")
    if text is None or trending_kb is None:
        await msg.edit_text(
            "❌ <b>Trending Unavailable</b>\n\nCould not fetch trending tokens right now.\nPlease try again later.",
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
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            r = await client.get(
                f"{BACKEND_URL}{path}",
                params=params,
                headers=backend_headers(),
            )
            if r.status_code != 200:
                logger.debug("market intel %s status %s", path, r.status_code)
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("market intel %s fetch failed: %s", path, exc)
        return None


def _menu_kb() -> InlineKeyboardMarkup:
    """Shared bottom bar: back to the main menu."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]
    )


def _change_emoji(change: float) -> str:
    return "\U0001f7e2" if change >= 0 else "\U0001f534"


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /market — global market overview (mcap, volume, dominance, fear/greed, BTC/ETH)."""
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f4ca <b>Fetching market overview...</b>", parse_mode=ParseMode.HTML
    )
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
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f195 <b>Fetching new launches...</b>", parse_mode=ParseMode.HTML
    )
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
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f40b <b>Fetching whale activity...</b>", parse_mode=ParseMode.HTML
    )
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
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f4f0 <b>Fetching news...</b>", parse_mode=ParseMode.HTML
    )
    articles = []
    source_label = "RMI News Network"

    # Try the internal news-feed endpoint first (has bias/sentiment/factual ratings)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/internal/news-feed",
                params={"channel": "news", "limit": 5, "min_score": 0},
                headers=backend_headers(),
            )
            if r.status_code == 200:
                body = r.json()
                articles = body.get("items", [])
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("cmd_news internal feed failed: %s", e)

    # Fallback to backend trending news
    if not articles:
        source_label = "Trending"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/news/trending?window_hours=168&limit=5",
                    headers=backend_headers(),
                )
                if r.status_code == 200:
                    body = r.json()
                    articles = body.get("items", [])
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
        ) as e:
            logger.warning("cmd_news backend fetch failed: %s", e)

    if not articles:
        cg_items = await _fetch_cg_trending_news()
        if cg_items:
            articles = cg_items
            source_label = "Trending"

    news_kb_rows: list[list[InlineKeyboardButton]] = []
    if TELEGRAM_CHANNEL:
        news_kb_rows.append(
            [InlineKeyboardButton("\U0001f4e2 Alerts Channel", url=TELEGRAM_CHANNEL)]
        )
    news_kb_rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data="news_refresh")])
    news_kb_rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    news_kb = InlineKeyboardMarkup(news_kb_rows)

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
            title_linked = f'<a href="{url}">{title}</a>' if url else title

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
                f"  <i>{source}</i>{pub_str} \u00b7 Factuality: {factual}"
            )
        lines.append(footer_links())
        text = "\n".join(lines)
    else:
        # [REVIEW] Branding scrubbed: the live bot's fallback hardcoded the
        # alerts handle + website label (checklist §6.15).
        # OSS uses the env-driven channel/website values with neutral labels;
        # when unset the links simply don't render.
        lines = ["\U0001f4f0 <b>Crypto News</b>\n\n<i>News feed temporarily unavailable.</i>"]
        if TELEGRAM_CHANNEL:
            lines.append(
                f'\U0001f4e2 Follow <a href="{TELEGRAM_CHANNEL}">our alerts channel</a> for real-time alerts!'
            )
        if WEBSITE_URL:
            lines.append(f'\U0001f310 <a href="{WEBSITE_URL}">our website</a>')
        text = "\n".join(lines)

    with contextlib.suppress(Exception):
        await msg.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=news_kb,
            disable_web_page_preview=True,
        )
