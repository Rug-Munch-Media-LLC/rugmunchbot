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
from config import BACKEND_URL, RMI_AUTH_TOKEN, WEB_APP_URL, WEBSITE_URL
from services.formatting import esc, sep
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# ── Main menu layout ──
MAIN_MENU_ROWS: list[list[tuple[str, str]]] = [
    [("🔍 Scan Token", "menu_scan"), ("🧠 Deep Scan", "menu_deepscan")],
    [("🤖 AI Analysis", "menu_analyze"), ("⚡ Quick Risk", "menu_risk")],
    [("📊 Market Overview", "menu_market"), ("📰 News", "menu_news")],
    [("👛 Wallet Check", "menu_wallet"), ("🔔 Alerts", "menu_alerts")],
    [("👤 Account", "menu_account"), ("⭐ Upgrade", "menu_pricing")],
    [("📚 Scam School", "menu_scamschool")],
]


def v3_main_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=cd) for label, cd in row]
        for row in MAIN_MENU_ROWS
    ]
    rows.append([InlineKeyboardButton("🌐 rugmunch.io", url=WEBSITE_URL)])
    return InlineKeyboardMarkup(rows)


def back_kb(label: str = "◀️ Main Menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="menu_main")]])


def web_scan_button(address: str, chain: str = "") -> InlineKeyboardButton:
    url = f"{WEB_APP_URL}?token={address}"
    if chain:
        url += f"&chain={chain}"
    return InlineKeyboardButton("🛡️ Full Report on Web", url=url)


def chart_url(address: str, chain: str) -> str:
    return f"https://dexscreener.com/{chain}/{address}"


def scan_result_kb_v3(address: str, chain: str) -> InlineKeyboardMarkup:
    """Scan-report actions: Deep Scan · AI Analysis · Chart · Watch · Web."""
    rows = [
        [
            InlineKeyboardButton("🔬 Deep Scan", callback_data=f"deep_{address}_{chain}"),
            InlineKeyboardButton("🤖 AI Analysis", callback_data=f"ai_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("📊 Chart", url=chart_url(address, chain)),
            InlineKeyboardButton("👀 Watch", callback_data=f"watch_{address}_{chain}"),
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
    r"^(menu_(scan|deepscan|analyze|risk|market|news|alerts|main)|"
    r"scan_|deep_|ai_|watch_|alerts_)"
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
            "🛡️ <b>RugMunch Intelligence</b>\n\nWhat would you like to do?",
            parse_mode=ParseMode.HTML,
            reply_markup=v3_main_menu_kb(),
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
    elif data == "menu_market":
        from handlers.market import display_market

        await display_market(query)
    elif data == "menu_news":
        await _show_news(query)
    elif data == "menu_alerts":
        from handlers.alerts import display_alerts

        await display_alerts(query, user.id)
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
