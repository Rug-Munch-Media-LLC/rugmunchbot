#!/usr/bin/env python3
"""
V3 menu system — inline keyboard navigation.

Main menu (per TELEGRAM-BOT-V3-DESIGN.md §3.2): Scan Token, Deep Scan,
AI Analysis, Quick Risk, Market, News, Wallet, Alerts, Account, Upgrade,
Scam School. Every screen exposes a "◀️ Main Menu" back button.

This module also owns the V3 callback dispatcher: menu_*, scan_, deep_,
ai_, watch_, alerts_ prefixes. It is registered BEFORE the legacy
handle_callback in bot.py so V3 prefixes win; legacy prefixes (sub_,
topup_, scam_, …) still fall through to the old handler.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import httpx
from config import (
    BACKEND_URL,
    DATABUS_URL,
    OWNER_IDS,
    RMI_AUTH_TOKEN,
    WEB_APP_URL,
    WEBSITE_URL,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from services.formatting import esc, sep, thin_sep
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# ── Main menu layout ──
MAIN_MENU_ROWS: list[list[tuple[str, str]]] = [
    [("🔥 Trending", "menu_trending"), ("📈 Top Volume", "menu_top")],
    [("📊 Chart", "menu_chart"), ("🗺️ Bubble Map", "menu_map")],
    [("🔍 Scan Token", "menu_scan"), ("🧠 Deep Scan", "menu_deepscan")],
    [("🤖 AI Analysis", "menu_analyze"), ("⚡ Quick Risk", "menu_risk")],
    [("📰 News", "menu_news"), ("😱 Fear & Greed", "menu_fear")],
    [("👛 Wallet Check", "menu_wallet"), ("👀 Watchlist", "menu_watchlist")],
    [("👤 Account", "menu_account"), ("⭐ Upgrade", "menu_pricing")],
    [("📚 Scam School", "menu_scamschool"), ("🤝 Refer", "menu_refer")],
]


def back_kb(label: str = "◀️ Main Menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="menu_main")]])


# ── Admin-only menu ──
ADMIN_MENU_ROWS: list[list[tuple[str, str]]] = [
    [("📢 Broadcast", "admin_broadcast"), ("📊 User Stats", "admin_userstats")],
    [("🏥 System Health", "admin_health"), ("🔄 Force Scan Cache", "admin_forcecache")],
    [("🧠 RAG Status", "admin_rag"), ("🔧 Toggle Maintenance", "admin_maintenance")],
]


def is_admin(user_id: int) -> bool:
    return user_id in OWNER_IDS


def admin_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=cd) for label, cd in row]
        for row in ADMIN_MENU_ROWS
    ]
    return InlineKeyboardMarkup(rows)


def v3_main_menu_kb(user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=cd) for label, cd in row]
        for row in MAIN_MENU_ROWS
    ]
    rows.append([InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)])
    if user_id and is_admin(user_id):
        rows.append([InlineKeyboardButton("─ 👑 Admin Panel ─", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def web_scan_button(address: str, chain: str = "") -> InlineKeyboardButton:
    url = f"{WEB_APP_URL}?token={address}"
    if chain:
        url += f"&chain={chain}"
    return InlineKeyboardButton("🛡️ Full Report on Web", url=url)


def chart_url(address: str, chain: str) -> str:
    return f"https://dexscreener.com/{chain}/{address}"


def scan_result_kb_v3(address: str, chain: str) -> InlineKeyboardMarkup:
    """Scan-report actions: Deep Scan · Chart · Map · AI Analysis · Watch · Web · Share."""
    share_url = f"https://t.me/share/url?url={WEBSITE_URL}/scanner?token={address}"
    rows = [
        [
            InlineKeyboardButton("🔬 Deep Scan", callback_data=f"deep_{address}_{chain}"),
            InlineKeyboardButton("🤖 AI Analysis", callback_data=f"ai_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("📊 Chart", callback_data=f"chart_{address}_{chain}"),
            InlineKeyboardButton("🗺️ Map", callback_data=f"mapaddr_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("👀 Watch", callback_data=f"watch_{address}_{chain}"),
            InlineKeyboardButton("📤 Share", url=share_url),
        ],
        [web_scan_button(address, chain)],
        [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(rows)


def pricing_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⭐ View Plans", callback_data="menu_pricing")],
        [InlineKeyboardButton("🔋 Buy Top-Up", callback_data="menu_topup")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(rows)


# ── News (menu_news) ──


async def _show_news(query) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/internal/news-feed",
                params={"limit": 5, "min_score": 0},
                headers={"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {},
            )
            if r.status_code == 200:
                data = r.json()
                items = data.get("items", []) if isinstance(data, dict) else (data or [])
                if items:
                    lines = ["📰 <b>Rated Crypto News</b>", sep(), ""]
                    for article in items[:5]:
                        title = esc(article.get("title", "Untitled")[:80])
                        source = esc(article.get("source", "News"))
                        factual = article.get("factual_rating") or article.get("quality_score")
                        fact = f" · Factuality: <b>{esc(factual)}</b>" if factual else ""
                        lines.append(f"🔥 <b>{title}</b>")
                        lines.append(f"  <i>{source}</i>{fact}")
                        lines.append("")
                    lines.append(sep())
                    lines.append(f'🌐 <a href="{WEBSITE_URL}">More on rugmunch.io</a>')
                    await query.edit_message_text(
                        "\n".join(lines),
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_kb(),
                        disable_web_page_preview=True,
                    )
                    return
    except Exception as exc:
        logger.debug("news fetch failed: %s", exc)
    await query.edit_message_text(
        "📰 <b>News unavailable</b>\n\nPlease try again later.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


# ── V3 callback dispatcher ──

V3_CALLBACK_PATTERN = (
    r"^(menu_(scan|deepscan|analyze|risk|market|news|alerts|main|trending|top|chart|map|fear|"
    r"wallet|watchlist|account|pricing|scamschool|refer)|"
    r"admin_panel|admin_broadcast|admin_userstats|admin_health|admin_forcecache|admin_rag|"
    r"admin_maintenance|"
    r"scan_|deep_|ai_|watch_|alerts_|mapaddr_|topchain_|chart_)"
)


async def handle_v3_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dispatch V3 callback prefixes (registered before the legacy handler)."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""
    user = query.from_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if data == "menu_main":
        await query.edit_message_text(
            "🛡️ <b>Rug Munch Intelligence</b>\n\n"
            "🧠 <b>Expert Chat</b> — /ask, /explain, /pulse\n"
            "📊 <b>Market</b> — Trending, top volume, fear index, news\n"
            "🔍 <b>Analysis</b> — Token scan, chart, map, wallet check\n"
            "🎓 <b>Learn</b> — Scam School, rug check, rehab course\n\n"
            "<i>Select an option below:</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=v3_main_menu_kb(user.id),
        )
    elif data == "menu_scan":
        await query.edit_message_text(
            "🔍 <b>Scan a Token</b>\n\n"
            "Send me a contract address or use:\n/scan <code>address</code>\n\n"
            "<i>RugMunchScan checks risk score, honeypots, liquidity and deployer history.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_deepscan":
        await query.edit_message_text(
            "🧠 <b>Deep Scan (SENTINEL)</b>\n\n"
            "Premium forensic analysis — deployer history, holder clusters, "
            "bundle detection, scam indicators.\n\n"
            "Send me a contract address or use:\n/deepscan <code>address</code>\n\n"
            "<i>Scout+ or ⭐250 per scan.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_analyze":
        await query.edit_message_text(
            "🤖 <b>AI Analysis</b>\n\n"
            "RugMunch AI (Qwen3.5-9B) analyses a token using scanner intelligence.\n\n"
            "Send me a contract address or use:\n/analyze <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_risk":
        await query.edit_message_text(
            "⚡ <b>Quick Risk</b>\n\n"
            "Lightweight risk score — no full report.\n\n"
            "Send me a contract address or use:\n/risk <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_trending":
        await _show_trending_inline(query)
    elif data == "menu_top":
        await _show_top_inline(query, "solana")
    elif data == "menu_chart":
        await query.edit_message_text(
            "📊 <b>Token Chart</b>\n\n"
            "See price, volume, and OHLCV data.\n\n"
            "Usage: /chart <code>address</code> [chain]\n\n"
            "<i>Also available from scan results.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_map":
        await query.edit_message_text(
            "🗺️ <b>Holder Bubble Map</b>\n\n"
            "Visualize holder distribution and wallet clusters.\n\n"
            "Usage: /map <code>address</code> [chain]\n\n"
            "<i>Also available from scan results.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "menu_fear":
        await _show_fear_inline(query)
    elif data == "menu_market":
        from handlers.market import display_market

        await display_market(query)
    elif data == "menu_news":
        await _show_news(query)
    elif data.startswith("scan_"):
        from handlers.scan import handle_scan_callback

        await handle_scan_callback(update, context)
    elif data.startswith("deep_"):
        from handlers.scan import handle_deep_callback

        await handle_deep_callback(update, context)
    elif data.startswith("ai_"):
        from handlers.analyze import handle_ai_callback

        await handle_ai_callback(update, context)
    elif data.startswith("watch_"):
        from handlers.scan import handle_watch_callback

        await handle_watch_callback(update, context)
    elif data.startswith("alerts_"):
        from handlers.alerts import handle_alerts_callback

        await handle_alerts_callback(update, context)
    elif data.startswith("mapaddr_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            await _show_map_inline(query, parts[1], parts[2])
    elif data.startswith("topchain_"):
        await _show_top_inline(query, data[9:])
    elif data.startswith("chart_"):
        parts = data.split("_", 2)
        if len(parts) >= 3:
            await _show_chart_inline(query, parts[1], parts[2])
    # ── Admin callbacks ──
    elif data == "admin_panel":
        if not is_admin(user.id):
            await query.answer("🔒 Admin only", show_alert=True)
            return
        await query.edit_message_text(
            "👑 <b>Admin Control Panel</b>\n\n<i>Select an admin action below:</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data == "admin_broadcast":
        if not is_admin(user.id):
            return
        await query.edit_message_text(
            "📢 <b>Broadcast Message</b>\n\n"
            "To broadcast to all users, use:\n/broadcast <code>message</code>\n\n"
            "<i>This will send a message to every registered user.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    elif data == "admin_userstats":
        if not is_admin(user.id):
            return
        await _show_admin_userstats(query)
    elif data == "admin_health":
        if not is_admin(user.id):
            return
        await _show_admin_health(query)
    elif data == "admin_forcecache":
        if not is_admin(user.id):
            return
        await _show_admin_forcecache(query)
    elif data == "admin_rag":
        if not is_admin(user.id):
            return
        await _show_admin_rag(query)
    elif data == "admin_maintenance":
        if not is_admin(user.id):
            return
        await query.edit_message_text(
            "🔧 <b>Maintenance Mode</b>\n\n"
            "Toggle maintenance: /maintenance on|off\n\n"
            "<i>When on, users see a maintenance message instead of scan results.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ══════════════════════════════════════════════
# INLINE HELPERS (called from menu callbacks)
# ══════════════════════════════════════════════


async def _show_trending_inline(query) -> None:
    """Show trending tokens inline from menu."""
    await query.edit_message_text(
        "🔥 <b>Fetching trending tokens...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            # Try RugCharts API first
            try:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/rugcharts/trending",
                    timeout=8,
                    headers={"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {},
                )
                if r.status_code == 200:
                    data = r.json()
                    tokens = data if isinstance(data, list) else data.get("tokens", [])
                    if tokens:
                        return await _render_trending_inline(query, tokens, from_rugcharts=True)
            except Exception:
                pass

            # Fall back to DexScreener
            r = await client.get("https://api.dexscreener.com/latest/dex/search/?q=trending")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])[:10]
                if pairs:
                    await _render_trending_inline(query, pairs, from_rugcharts=False)
                    return
    except Exception as e:
        logger.debug("_show_trending_inline failed: %s", e)
    await query.edit_message_text(
        "❌ Trending data unavailable.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


async def _render_trending_inline(query, tokens: list, from_rugcharts: bool = False) -> None:
    """Render trending token list from either API source."""
    lines = ["🔥 <b>Trending Tokens</b>", sep()]
    for i, t in enumerate(tokens[:10], 1):
        if from_rugcharts:
            name = t.get("name", "???")[:20]
            symbol = t.get("symbol", "???")
            price = float(t.get("price_usd") or t.get("price") or 0)
            h24 = float(t.get("change_24h") or 0)
            vol = float(t.get("volume_24h") or 0)
            addr = t.get("address", "")
            chain = t.get("chain", "")
        else:
            bt = t.get("baseToken", {})
            name = bt.get("name", "???")[:20]
            symbol = bt.get("symbol", "???")
            price = float(t.get("priceUsd", 0))
            h24 = t.get("priceChange", {}).get("h24") or 0
            vol = float(t.get("volume", {}).get("h24") or 0)
            addr = bt.get("address", "")
            chain = t.get("chainId", "")
        emoji = "🟢" if h24 >= 0 else "🔴"
        rank = ["❶", "❷", "❸"][i - 1] if i <= 3 else f"{i}."
        lines.append(
            f"{rank} {emoji} <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
            f"   💰 {fmt_number(price)} | 24h: {fmt_pct(h24)} | Vol: {fmt_number(vol)}"
        )
    lines.append(f'\n{thin_sep()}\n🌐 <a href="{WEB_APP_URL}">View all on rugmunch.io</a>')
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Menu", callback_data="menu_main")]])
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def _show_top_inline(query, chain: str) -> None:
    """Show top volume rankings for a chain."""
    await query.edit_message_text(
        f"📈 <b>Fetching top {chain.capitalize()} tokens...</b>",
        parse_mode=ParseMode.HTML,
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                f"https://api.dexscreener.com/latest/dex/search/?q=trending%20{chain}"
            )
            if r.status_code != 200:
                raise RuntimeError("API error")
            pairs = r.json().get("pairs", [])[:10]
            if not pairs:
                await query.edit_message_text(
                    f"❌ No data for chain: {chain}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return

            lines = [f"📈 <b>Top {chain.capitalize()} by Volume</b>", sep()]
            for i, p in enumerate(pairs, 1):
                bt = p.get("baseToken", {})
                name = bt.get("name", "???")[:20]
                symbol = bt.get("symbol", "???")
                price = float(p.get("priceUsd", 0))
                h24 = p.get("priceChange", {}).get("h24") or 0
                vol = float(p.get("volume", {}).get("h24") or 0)
                addr = bt.get("address", "")
                emoji = "🟢" if h24 >= 0 else "🔴"
                rank_badge = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"{i}."
                lines.append(
                    f"{rank_badge} {emoji} <b>{name}</b> ({symbol})\n"
                    f"   💰 {fmt_number(price)} | 24h: {fmt_pct(h24)}\n"
                    f"   📊 Vol: {fmt_number(vol)}"
                )

            chain_buttons = [
                InlineKeyboardButton("◎ SOL", callback_data="topchain_solana"),
                InlineKeyboardButton("⟠ ETH", callback_data="topchain_ethereum"),
                InlineKeyboardButton("🔷 BASE", callback_data="topchain_base"),
                InlineKeyboardButton("🔶 BSC", callback_data="topchain_bsc"),
            ]
            kb = InlineKeyboardMarkup(
                [
                    chain_buttons,
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            )
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
    except Exception as e:
        logger.debug("_show_top_inline failed: %s", e)
        await query.edit_message_text(
            f"❌ Rankings unavailable: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _show_fear_inline(query) -> None:
    """Show Fear & Greed Index inline."""
    await query.edit_message_text("😱 <b>Loading Fear & Greed...</b>", parse_mode=ParseMode.HTML)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.alternative.me/fng/?limit=1")
            if r.status_code == 200:
                data_list = r.json().get("data", [])
                if data_list:
                    d = data_list[0]
                    val = int(d.get("value", 50))
                    classification = d.get("value_classification", "Neutral")
                    ts = int(d.get("timestamp", 0))
                    from datetime import UTC, datetime

                    timestamp = datetime.fromtimestamp(ts, tz=UTC).strftime("%b %d, %Y")
                    if val <= 25:
                        emoji = "😱"
                        tag = "Extreme Fear"
                    elif val <= 45:
                        emoji = "😨"
                        tag = "Fear"
                    elif val <= 55:
                        emoji = "😐"
                        tag = "Neutral"
                    elif val <= 75:
                        emoji = "😊"
                        tag = "Greed"
                    else:
                        emoji = "🤑"
                        tag = "Extreme Greed"
                    bar = "🟥" * (val // 15) + "⬜" * (6 - val // 15)
                    text = (
                        f"{emoji} <b>Crypto Fear & Greed Index</b>\n{sep()}\n\n"
                        f"<b>Index:</b> {val}/100 — {tag}\n"
                        f"<b>Classification:</b> {classification}\n"
                        f"<b>Updated:</b> {timestamp}\n\n"
                        f"{bar}\n\n"
                        f"<i>Extreme Fear = opportunity | Extreme Greed = caution</i>"
                    )
                    await query.edit_message_text(
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_kb(),
                    )
                    return
    except Exception as e:
        logger.debug("_show_fear_inline failed: %s", e)
    await query.edit_message_text(
        "😱 <b>Fear & Greed Index</b>\n\nCould not fetch live data.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


async def _show_map_inline(query, address: str, chain: str) -> None:
    """Show holder bubble map from callback."""
    await query.edit_message_text(
        f"🗺️ <b>Loading holder data...</b>\n<code>{address[:10]}...</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            map_data = None
            holders_info = ""
            # Try RugMaps endpoint
            try:
                r = await client.post(
                    f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
                    json={"chain": chain, "address": address},
                    timeout=10,
                    headers={"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {},
                )
                if r.status_code == 200:
                    map_data = r.json()
            except Exception:
                pass

            if not map_data:
                try:
                    r = await client.get(
                        f"{DATABUS_URL}/token/{address}",
                        timeout=8,
                        headers={"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {},
                    )
                    if r.status_code == 200:
                        payload = (
                            r.json().get("data", r.json()) if isinstance(r.json(), dict) else {}
                        )
                        detail = payload if isinstance(payload, dict) else {}
                        holders = detail.get("top_holders") or detail.get("holders", [])
                        if holders:
                            total = detail.get("holders_count") or len(holders)
                            holders_info = (
                                f"👥 <b>Total Holders:</b> {total}\n"
                                f"{thin_sep()}\n<b>Top Holders:</b>\n"
                            )
                            for i, h in enumerate(holders[:10], 1):
                                h_addr = h.get("address") or h.get("holder", "")
                                h_pct = h.get("pct") or h.get("percentage") or h.get("share", 0)
                                tag = f" [{h.get('tag', '')}]" if h.get("tag") else ""
                                holders_info += (
                                    f"  {i}. <code>{h_addr[:6]}...{h_addr[-4:]}</code>"
                                    f" — {float(h_pct):.1f}%{tag}\n"
                                )
                except Exception:
                    pass

            if not map_data and not holders_info:
                await query.edit_message_text(
                    "❌ <b>Map Data Unavailable</b>\n\n"
                    f'🌐 <a href="{WEBSITE_URL}/rugmaps?token={address}">View on website</a>',
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                    disable_web_page_preview=True,
                )
                return

            lines = [f"🗺️ <b>Holder Bubble Map</b>\n{sep()}"]
            if map_data:
                name = map_data.get("name", "")
                symbol = map_data.get("symbol", "")
                if name:
                    lines.append(f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}")
                lines.append(f"<code>{address}</code>\n")
                th = map_data.get("total_holders") or map_data.get("holders_count", 0)
                cl = map_data.get("clusters") or map_data.get("wallet_clusters", [])
                t10 = map_data.get("top10_holder_pct") or map_data.get("top10_concentration", 0)
                lines.append(f"👥 Total Holders: <b>{th}</b>")
                lines.append(f"📊 Top 10 Concentration: <b>{float(t10):.1f}%</b>")
                if cl:
                    lines.append(f"🕸️ Wallet Clusters: <b>{len(cl)}</b>")
                lines.append("")

            if holders_info:
                lines.append(holders_info)
            elif map_data:
                holders = map_data.get("top_holders") or map_data.get("holders", [])
                if holders:
                    lines.append(f"{thin_sep()}\n<b>Top Holders:</b>")
                    for i, h in enumerate(holders[:10], 1):
                        h_addr = h.get("address") or h.get("holder", "")
                        h_pct = h.get("pct") or h.get("percentage") or h.get("share", 0)
                        tag = f" [{h.get('tag', '')}]" if h.get("tag") else ""
                        lines.append(
                            f"  {i}. <code>{h_addr[:6]}...{h_addr[-4:]}</code>"
                            f" — {float(h_pct):.1f}%{tag}"
                        )

            lines.append(
                f"\n{thin_sep()}\n"
                f'🌐 <a href="{WEBSITE_URL}/rugmaps?token={address}">Full bubble map on rugmunch.io</a>'
            )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🗺️ Full Map", url=f"{WEBSITE_URL}/rugmaps?token={address}"
                        )
                    ],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            )
            await query.edit_message_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Map error: {str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _show_chart_inline(query, address: str, chain: str) -> None:
    """Show token chart inline from callback."""
    await query.edit_message_text(
        f"📊 <b>Loading chart data...</b>\n<code>{address[:10]}...</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                f"{DATABUS_URL}/fetch/token_price",
                params={"mint": address},
                timeout=8,
            )
            if r.status_code != 200:
                await query.edit_message_text(
                    "❌ Could not fetch token data.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return
            payload = r.json().get("data", r.json()) if isinstance(r.json(), dict) else {}
            pairs = payload.get("pairs", []) if isinstance(payload, dict) else []
            if not pairs:
                await query.edit_message_text(
                    "❌ No trading pairs found.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
                return
            p = pairs[0]
            name = p.get("baseToken", {}).get("name", "Unknown")
            symbol = p.get("baseToken", {}).get("symbol", "???")
            price = float(p.get("priceUsd", 0))
            pc = p.get("priceChange", {})
            h24 = pc.get("h24") or 0
            h6 = pc.get("h6") or 0
            d7 = pc.get("d7") or 0
            vol = p.get("volume", {})
            v24 = float(vol.get("h24") or 0)
            liq_data = p.get("liquidity", {})
            liq = float(liq_data.get("usd") or 0)
            mcap = float(p.get("marketCap") or p.get("fdv") or 0)
            pair_addr = p.get("pairAddress", "")

            text = (
                f"📊 <b>Token Chart</b>\n{sep()}\n"
                f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                f"<code>{address}</code>\n\n"
                f"💰 Price: <b>{fmt_number(price)}</b>\n"
                f"📈 6h: {fmt_pct(h6)} | 24h: {fmt_pct(h24)} | 7d: {fmt_pct(d7)}\n"
                f"💧 Liquidity: <b>{fmt_number(liq)}</b>\n"
                f"📊 Volume 24h: <b>{fmt_number(v24)}</b>\n"
                f"🏷️ MCap: <b>{fmt_number(mcap)}</b>\n"
            )
            # Try OHLCV
            try:
                ohlcv_r = await client.get(
                    f"{BACKEND_URL}/api/v1/rugcharts/ohlcv/{chain}/{pair_addr or address}",
                    timeout=5,
                    headers={"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {},
                )
                if ohlcv_r.status_code == 200:
                    ohlcv_data = ohlcv_r.json()
                    if ohlcv_data:
                        text += "\n📈 <b>Recent Candles (15m):</b>\n"
                        candles = ohlcv_data[:5] if isinstance(ohlcv_data, list) else []
                        for c in candles:
                            o = float(c.get("open", 0))
                            cl = float(c.get("close", 0))
                            hi = float(c.get("high", 0))
                            lo = float(c.get("low", 0))
                            dir_emoji = "🟢" if cl >= o else "🔴"
                            text += (
                                f"  {dir_emoji} O:{fmt_number(o)} H:{fmt_number(hi)} "
                                f"L:{fmt_number(lo)} C:{fmt_number(cl)}\n"
                            )
            except Exception:
                pass

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📊 Full Chart", url=f"{WEBSITE_URL}/rugcharts?token={address}"
                        ),
                        InlineKeyboardButton("🗺️ Map", callback_data=f"mapaddr_{address}_{chain}"),
                    ],
                    [
                        InlineKeyboardButton("🔍 Scan", callback_data=f"scan_{address}_{chain}"),
                        InlineKeyboardButton(
                            "📈 DexScreener", url=f"https://dexscreener.com/{chain}/{address}"
                        ),
                    ],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            )
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Chart error: {str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ══════════════════════════════════════════════
# ADMIN HELPERS
# ══════════════════════════════════════════════


async def _show_admin_userstats(query) -> None:
    """Admin: show DB user statistics."""
    await query.edit_message_text("⏳ <b>Loading user stats...</b>", parse_mode=ParseMode.HTML)
    try:
        conn = db.get_db()
        total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
        total_users = total["c"] if total else 0
        tiers = conn.execute(
            "SELECT tier, COUNT(*) as cnt FROM users WHERE tier IS NOT NULL GROUP BY tier"
        ).fetchall()
        scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()
        total_scans = scans["c"] if scans else 0
        active24h = conn.execute(
            "SELECT COUNT(DISTINCT user_id) as c FROM scan_logs WHERE created_at > datetime('now', '-1 day')"
        ).fetchone()
        active24 = active24h["c"] if active24h else 0
        conn.close()

        lines = [
            "📊 <b>Admin: User Statistics</b>",
            sep(),
            "",
            f"👥 Total Users: <b>{total_users:,}</b>",
            f"🔍 Total Scans: <b>{total_scans:,}</b>",
            f"📱 Active 24h: <b>{active24:,}</b>",
            "",
            "📦 <b>Tier Breakdown:</b>",
            thin_sep(),
        ]
        for t in tiers:
            lines.append(f"  • {t['tier']}: <b>{t['cnt']:,}</b> users")
        lines.extend(["", sep()])
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ User stats error: {esc(str(e)[:200])}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _show_admin_health(query) -> None:
    """Admin: show system health overview."""
    await query.edit_message_text("⏳ <b>Checking system health...</b>", parse_mode=ParseMode.HTML)
    try:
        import psutil

        async with httpx.AsyncClient(timeout=8) as client:
            # Check backend
            try:
                r = await client.get(f"{BACKEND_URL}/api/v1/health")
                be_ok = r.status_code == 200
            except Exception:
                be_ok = False
            # Check DataBus
            try:
                r = await client.get(
                    f"{DATABUS_URL}/fetch/token_price",
                    params={"mint": "So11111111111111111111111111111111111111112"},
                    timeout=5,
                )
                db_ok = r.status_code in (200, 404)  # 404 = endpoint exists, just no data
            except Exception:
                db_ok = False

        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu = psutil.cpu_percent(0.5)

        lines = [
            "🏥 <b>Admin: System Health</b>",
            sep(),
            "",
            f"🔗 Backend: {'🟢 ONLINE' if be_ok else '🔴 OFFLINE'}",
            f"📡 DataBus: {'🟢 ONLINE' if db_ok else '🔴 OFFLINE'}",
            f"💾 Disk: {disk.used / (1024**3):.1f}G/{disk.total / (1024**3):.1f}G ({disk.percent}%)",
            f"🧠 Mem: {mem.used / (1024**3):.1f}G/{mem.total / (1024**3):.1f}G ({mem.percent}%)",
            f"💻 CPU: {cpu:.0f}% ({psutil.cpu_count()} cores)",
            "",
            sep(),
        ]
        await query.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Health check error: {esc(str(e)[:200])}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _show_admin_forcecache(query) -> None:
    """Admin: force refresh scan cache."""
    await query.edit_message_text("⏳ <b>Flushing scan cache...</b>", parse_mode=ParseMode.HTML)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{BACKEND_URL}/api/v2/scanner/cache/refresh",
                headers={"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {},
            )
            if r.status_code == 200:
                result = "✅ Cache refresh triggered successfully"
            else:
                result = f"⚠️ Backend responded: {r.status_code}"
    except Exception as e:
        result = f"❌ Cache refresh failed: {esc(str(e)[:200])}"
    await query.edit_message_text(
        f"🔄 <b>Scan Cache</b>\n{sep()}\n\n{result}",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


async def _show_admin_rag(query) -> None:
    """Admin: show RAG document status."""
    await query.edit_message_text("⏳ <b>Checking RAG status...</b>", parse_mode=ParseMode.HTML)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/rag/v2/status",
                headers={"X-API-Key": RMI_AUTH_TOKEN} if RMI_AUTH_TOKEN else {},
            )
            if r.status_code == 200:
                data = r.json()
                lines = [
                    "🧠 <b>Admin: RAG Status</b>",
                    sep(),
                    "",
                ]
                if isinstance(data, dict):
                    for k, v in data.items():
                        lines.append(f"  • <b>{esc(str(k))}</b>: {esc(str(v)[:100])}")
                else:
                    lines.append(f"<pre>{esc(str(data)[:2000])}</pre>")
                lines.extend(["", sep()])
            else:
                lines = ["🧠 <b>RAG Status</b>", sep(), "", f"⚠️ API returned: {r.status_code}"]
    except Exception as e:
        lines = ["🧠 <b>RAG Status</b>", sep(), "", f"❌ Error: {esc(str(e)[:200])}"]
    await query.edit_message_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )
