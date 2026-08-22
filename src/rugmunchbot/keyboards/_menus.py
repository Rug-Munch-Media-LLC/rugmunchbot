#!/usr/bin/env python3
"""Main welcome menu and per-category submenu keyboards."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL
from rugmunchbot.formatters import is_owner
from rugmunchbot.handlers._help_text import CODEBERG_ORG


def _main_menu_rows(user_id: int | None = None) -> list[list[InlineKeyboardButton]]:
    """Shared main-menu rows (v11 — core intents only, decluttered).

    Six primary capability sections, two buttons per row — most-used
    actions on top:
      Scan         · Intelligence
      Alerts       · Wallet
      Account      · AI Chat
      Help

    Every registered capability is reachable from one of the sections:
      Scan          → security audits / deep / SENTINEL / rug checks
      Intelligence  → market / trending / movers / launches / whales /
                      news / sentiment / social / research / ask / chat
      Alerts        → price alerts / watchlist / whale alerts / webhooks
      Wallet        → wallet forensics / portfolio / watchlist / simulate
      Account       → dashboard / pricing / referrals / scam school
      Help          → full command reference / community links

    Channel + website URL rows were folded into the Help submenu so the
    welcome keyboard stays a grouped intent hub — every tap is one step
    into a real flow.

    Every callback_data below has a handler in
    handlers/callback/__init__.py — do NOT rename them.

    Owner (@cryptorugmunch) additionally sees an Admin button that
    opens the admin sub-menu — hidden from all other users.
    """
    rows = [
        [
            InlineKeyboardButton("\U0001f50d Scan", callback_data="menu_cat_scan"),
            InlineKeyboardButton("\U0001f9e0 Intelligence", callback_data="menu_cat_intel"),
        ],
        [
            InlineKeyboardButton("\U0001f514 Alerts", callback_data="menu_cat_alerts"),
            InlineKeyboardButton("\U0001f45b Wallet", callback_data="menu_cat_wallet"),
        ],
        [
            InlineKeyboardButton("\U0001f464 Account", callback_data="menu_cat_account"),
            InlineKeyboardButton("\U0001f916 AI Chat", callback_data="menu_cat_chat"),
        ],
        [
            InlineKeyboardButton("\U0001f4d6 Help", callback_data="menu_cat_help"),
        ],
    ]
    # Owner-only admin button — hidden from everyone else.
    if user_id is not None and is_owner(user_id):
        rows.append(
            [InlineKeyboardButton("\U0001f6e1\ufe0f Admin", callback_data="menu_cat_admin")]
        )
    return rows


def main_menu_kb(user_id: int | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(_main_menu_rows(user_id))


# ══════════════════════════════════════════════
# v5 — WORLD-CLASS MENU SYSTEM
# Categorized welcome menu + per-category submenus.
# Every submenu has a back button to menu_main.
# ══════════════════════════════════════════════


def welcome_menu_kb(user_id: int | None = None) -> InlineKeyboardMarkup:
    """Main welcome menu — five capability sections (v9).

    Scan · Intelligence · Alerts · Wallet · Help

    Owner (@cryptorugmunch) additionally sees an Admin button that
    opens the admin sub-menu — hidden from all other users.
    """
    return InlineKeyboardMarkup(_main_menu_rows(user_id))


def menu_kb() -> InlineKeyboardMarkup:
    """/menu quick-action keyboard (v14 — core intents, decluttered).

    Two buttons per row, one tap → the actual command/flow: Scan · Trending
    · Price · Wallet · Alerts · Ask AI · Help · Main Menu. Every callback
    below reuses an EXISTING wired surface (``run_*`` command cards / live
    inline data, ``menu_cat_chat`` AI welcome card, ``menu_main``) — zero
    new callback handlers needed. Do NOT rename the callback_data values.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f50d Scan a token", callback_data="run_scan"),
                InlineKeyboardButton("\U0001f525 Trending", callback_data="run_trending"),
            ],
            [
                InlineKeyboardButton("\U0001f4b0 Price", callback_data="run_price"),
                InlineKeyboardButton("\U0001f45b Wallet", callback_data="run_wallet"),
            ],
            [
                InlineKeyboardButton("\U0001f514 Alerts", callback_data="run_alerts"),
                InlineKeyboardButton("\U0001f916 Ask AI", callback_data="menu_cat_chat"),
            ],
            [
                InlineKeyboardButton("\U0001f4d6 Full Help", callback_data="run_help"),
                InlineKeyboardButton("\U0001f3e0 Main Menu", callback_data="menu_main"),
            ],
        ]
    )


def _menu_row(label: str, cb: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(label, callback_data=cb)]


def category_scan_kb() -> InlineKeyboardMarkup:
    """🔍 Scan submenu (v8).

    Security audits: Quick Scan (fast) · Deep Scan (full forensic) ·
    SENTINEL Scan (9-module) · Rug Check · Deployer Check · Hype ·
    Compare · Investigate (Pro). Wallet/Portfolio/Watchlist/Alerts
    moved to the Wallet group (v8).
    """
    return InlineKeyboardMarkup(
        [
            _menu_row("\u26a1 Quick Scan (fast)", "run_scan"),
            _menu_row("\U0001f52c Deep Scan (full forensic)", "run_deepscan"),
            _menu_row("\U0001f6e1\ufe0f AEGIS Scan (9-module)", "run_sentinelscan"),
            [
                InlineKeyboardButton("\U0001f6a8 Rug Check", callback_data="run_rugcheck"),
                InlineKeyboardButton("\U0001f575\ufe0f Deployer", callback_data="run_deployer"),
            ],
            [
                InlineKeyboardButton("\U0001f525 Hype", callback_data="run_hype"),
                InlineKeyboardButton("\u2696\ufe0f Compare", callback_data="run_compare"),
            ],
            _menu_row("\U0001f50e Investigate (Pro)", "run_investigate"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_charts_kb() -> InlineKeyboardMarkup:
    """📈 RugCharts submenu (v7 — charting-focused; RugMaps split out)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4c8 /chart — Price chart image", "run_chart"),
            _menu_row("\U0001f4ca /ta — Technical analysis & signals", "run_ta"),
            _menu_row("\U0001f4b0 /price — Token price", "run_price"),
            _menu_row("\U0001f4e6 /dex — DEX pairs & liquidity", "run_dex"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_rugmaps_kb() -> InlineKeyboardMarkup:
    """🗺️ RugMaps submenu (v7 — bubble maps & holder clusters)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f5fa\ufe0f /rugmap — Bubble map image", "run_rugmap"),
            _menu_row("\U0001f465 /holders — Top holders & clusters (Scout+)", "run_holders"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_market_kb() -> InlineKeyboardMarkup:
    """📊 Market submenu (v8).

    Market overview, trending, movers, launches, whales, charts, price,
    DEX, TA, holders, smart money and fund trace. All /market commands
    are wired: market · trending · movers · launches · whales · chart.
    """
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4ca Market Overview", "run_market"),
            [
                InlineKeyboardButton("\U0001f525 Trending", callback_data="run_trending"),
                InlineKeyboardButton("\U0001f680 Movers", callback_data="run_movers"),
            ],
            [
                InlineKeyboardButton("\U0001f195 Launches", callback_data="run_launches"),
                InlineKeyboardButton("\U0001f40b Whales", callback_data="run_whales"),
            ],
            [
                InlineKeyboardButton("\U0001f4c8 Chart", callback_data="run_chart"),
                InlineKeyboardButton("\U0001f4b0 Price", callback_data="run_price"),
            ],
            [
                InlineKeyboardButton("\U0001f4ca TA", callback_data="run_ta"),
                InlineKeyboardButton("\U0001f4e6 DEX & Liquidity", callback_data="run_dex"),
            ],
            [
                InlineKeyboardButton("\U0001f465 Holders", callback_data="run_holders"),
                InlineKeyboardButton("\U0001f9e0 Smart Money", callback_data="run_smartmoney"),
            ],
            [
                InlineKeyboardButton("\U0001f4e3 Social Signals", callback_data="run_social"),
                InlineKeyboardButton("\U0001f50d Fund Trace", callback_data="run_trace"),
            ],
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_wallet_kb() -> InlineKeyboardMarkup:
    """👛 Wallet submenu (v9).

    Wallet forensics, portfolio, watchlist and trade simulator. Price
    alerts moved to the dedicated Alerts section (v9).
    """
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f45b Wallet Analysis", "run_wallet"),
            [
                InlineKeyboardButton("\U0001f4c1 Portfolio", callback_data="run_portfolio"),
                InlineKeyboardButton("\U0001f440 Watchlist", callback_data="run_watchlist"),
            ],
            [
                InlineKeyboardButton("\U0001f4b5 Simulate", callback_data="run_simulate"),
            ],
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_intel_kb() -> InlineKeyboardMarkup:
    """🧠 Intelligence submenu (v9 — full knowledge + market section).

    Market overview, trending, movers, launches, whales, news, sentiment,
    social signals, RAG research, deep answers and freestyle AI chat.
    Market commands moved here from the old Market group (v9).
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f4ca Market", callback_data="run_market"),
                InlineKeyboardButton("\U0001f525 Trending", callback_data="run_trending"),
            ],
            [
                InlineKeyboardButton("\U0001f680 Movers", callback_data="run_movers"),
                InlineKeyboardButton("\U0001f195 Launches", callback_data="run_launches"),
            ],
            [
                InlineKeyboardButton("\U0001f40b Whales", callback_data="run_whales"),
                InlineKeyboardButton("\U0001f4f0 News", callback_data="run_news"),
            ],
            [
                InlineKeyboardButton("\U0001f4c8 Sentiment", callback_data="run_sentiment"),
                InlineKeyboardButton("\U0001f4e3 Social", callback_data="run_social"),
            ],
            [
                InlineKeyboardButton("\U0001f9e0 Research", callback_data="run_research"),
                InlineKeyboardButton("\U0001f50d Ask", callback_data="run_ask"),
            ],
            [InlineKeyboardButton("\U0001f4ac Chat", callback_data="run_chat")],
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_alerts_kb() -> InlineKeyboardMarkup:
    """🔔 Alerts submenu (v9 — dedicated section).

    Price & activity alerts, watchlist management, whale movement alerts
    and custom webhooks. Watchlist commands moved here from the Wallet
    group so Alerts is a first-class section.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f514 Alerts", callback_data="run_alerts"),
                InlineKeyboardButton("\U0001f4c4 Set Alert", callback_data="run_alert"),
            ],
            [
                InlineKeyboardButton("\U0001f440 Watchlist", callback_data="run_watchlist"),
                InlineKeyboardButton("\u2b50 Watch", callback_data="run_watch"),
            ],
            [
                InlineKeyboardButton("\U0001f40b Whale Alerts", callback_data="run_whale"),
                InlineKeyboardButton("\U0001f195 Launches", callback_data="run_launches"),
            ],
            [InlineKeyboardButton("\U0001f517 Webhooks (Pro)", callback_data="run_webhook")],
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_ai_kb() -> InlineKeyboardMarkup:
    """💬 AI Chat submenu (v6 — includes /chat reset hint)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4ac /chat — Freestyle AI chat", "run_chat"),
            _menu_row("\U0001f50d /ask — Deep RAG answer", "run_ask"),
            _menu_row("\U0001f9e0 /research — RAG deep research (Hunter+)", "run_research"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_portfolio_kb() -> InlineKeyboardMarkup:
    """📋 Portfolio submenu."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4c1 /portfolio — Wallet holdings", "run_portfolio"),
            _menu_row("\U0001f440 /watchlist — Tracked tokens", "run_watchlist"),
            _menu_row("\U0001f514 /alerts — Price alerts", "run_alerts"),
            _menu_row("\U0001f4b5 /simulate — Simulate a trade", "run_simulate"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_security_kb() -> InlineKeyboardMarkup:
    """🛡️ Security & Education submenu (v6 — new category)."""
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f6a8 /rugcheck — Rug pull checklist", "run_rugcheck"),
            _menu_row("\U0001f4da /scamschool — Learn scam patterns", "run_scamschool"),
            _menu_row("\U0001f6ab /report — Report a scam address", "run_report"),
            _menu_row("\U0001f575\ufe0f /investigate — Deep report (Pro)", "run_investigate"),
            _menu_row("\U0001f512 /scan — Security audit", "run_scan"),
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_account_kb() -> InlineKeyboardMarkup:
    """👤 Account submenu (v8).

    Account dashboard, pricing, top-up, buy stars, referrals and scam
    school. Uses the rich menu_* handlers (menu_account, menu_pricing,
    menu_refer, menu_scamschool) instead of generic run_* cards.
    """
    return InlineKeyboardMarkup(
        [
            _menu_row("\U0001f4cb Account Dashboard", "menu_account"),
            [
                InlineKeyboardButton("\u2b50 Pricing", callback_data="menu_pricing"),
                InlineKeyboardButton("\U0001f50b Top Up", callback_data="menu_topup"),
            ],
            [
                InlineKeyboardButton("\U0001f91d Refer", callback_data="menu_refer"),
                InlineKeyboardButton("\U0001f4da Scam School", callback_data="menu_scamschool"),
            ],
            [InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")],
        ]
    )


def category_help_kb() -> InlineKeyboardMarkup:
    """Help & About submenu.

    Command reference, then the community channels (Alerts / Alpha / News)
    as direct URL buttons so users can join the feeds without leaving the
    chat. Channels resolve from ``config.CHANNELS``; a channel with no
    resolvable link is omitted.
    """
    rows = [
        _menu_row("\U0001f4d6 /help — Full command reference", "run_help"),
        _menu_row("\U0001f4f1 Telegram Channel", "open_channel"),
        _menu_row("\U0001f4ac Community Chat", "open_group"),
        _menu_row("\U0001f310 Website", "open_website"),
        [InlineKeyboardButton("\U0001f419 Open Source", url=CODEBERG_ORG)],
    ]
    channels_row = []
    for key, label in (
        ("alerts", "\U0001f4e2 Alerts"),
        ("alpha", "\U0001f680 Alpha"),
        ("news", "\U0001f4f0 News"),
    ):
        url = _channel_public_url(key)
        if url is not None:
            channels_row.append(InlineKeyboardButton(label, url=url))
    if channels_row:
        rows.append(channels_row)
    rows.append([InlineKeyboardButton("\U0001f519 Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def _channel_public_url(key: str) -> str | None:
    """Telegram link for a ``config.CHANNELS`` channel (None when unresolvable).

    ``alerts`` uses the verified ``TELEGRAM_CHANNEL`` handle; ``alpha`` is
    the ``@rmialpha`` public handle; other channels fall back to the
    ``https://t.me/c/<id>`` deep link derived from their numeric chat id.
    """
    if key == "alerts":
        return TELEGRAM_CHANNEL
    if key == "alpha":
        return "https://t.me/rmialpha"
    raw = str(CHANNELS.get(key) or "")
    if raw.startswith("-100") and raw[4:].isdigit():
        return f"https://t.me/c/{raw[4:]}"
    return None
