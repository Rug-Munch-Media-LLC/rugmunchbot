#!/usr/bin/env python3
"""
RugMunchBot -- Keyboard builders.

All inline keyboard markup generators in one place.
"""

from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.domains.telegram.rugmunchbot.config import (
    BUY_STARS_PACKS,
    CHANNELS,
    SCAM_SCHOOL_TOPICS,
    SUPPORT_EMAIL,
    TELEGRAM_CHANNEL,
    TELEGRAM_GROUP,
    TIERS,
    TOP_UP_PACKS,
    TWITTER_URL,
    WEBSITE_URL,
)
from app.domains.telegram.rugmunchbot.formatters import (
    dexscreener_url,
    explorer_url,
    is_owner,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.handlers._help_text import CODEBERG_ORG


def token_buttons(address: str, chain: str, *, include_scan: bool = True) -> list[InlineKeyboardButton]:
    """Tap-to-action row for any token display: [📊 Scan] [📈 Chart] [🔍 Explorer].

    Scan re-runs the security scan via the ``scan_`` callback; Chart opens
    the DexScreener page; Explorer opens the block explorer (Solscan /
    Etherscan-family by chain). Full addresses stay inside the message text
    in ``<code>`` blocks — button labels only ever carry the short form.
    """
    buttons: list[InlineKeyboardButton] = []
    if include_scan:
        buttons.append(InlineKeyboardButton("\U0001f4ca Scan", callback_data=f"scan_{address}_{chain}"))
    buttons.append(InlineKeyboardButton("\U0001f4c8 Chart", url=dexscreener_url(address, chain)))
    buttons.append(InlineKeyboardButton("\U0001f50d Explorer", url=explorer_url(address, chain)))
    return buttons


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
        rows.append([InlineKeyboardButton("\U0001f6e1\ufe0f Admin", callback_data="menu_cat_admin")])
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
    for key, label in (("alerts", "\U0001f4e2 Alerts"), ("alpha", "\U0001f680 Alpha"), ("news", "\U0001f4f0 News")):
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


def admin_kb() -> InlineKeyboardMarkup:
    """Owner admin dashboard keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f4ca Stats", callback_data="admin_stats"),
                InlineKeyboardButton("\U0001f465 User Lookup", callback_data="admin_user_prompt"),
            ],
            [
                InlineKeyboardButton("\U0001f4e2 Broadcast", callback_data="admin_broadcast_prompt"),
                InlineKeyboardButton("\U0001f4dd Logs", callback_data="admin_logs"),
            ],
            [
                InlineKeyboardButton("\U0001f6e1\ufe0f Bot ON/OFF", callback_data="admin_toggle_bot"),
                InlineKeyboardButton("\U0001f4ac Chat ON/OFF", callback_data="admin_toggle_chat"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def owner_admin_reply_kb() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard pinned to the input field for the owner (v7).

    Shows the admin commands as tappable buttons in every private chat with
    the bot. The button labels are plain text; the message handler routes
    them to the matching /admin_* command (see handlers/message.py).
    """
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("\U0001f6e1\ufe0f Admin Dashboard"),
                KeyboardButton("\U0001f4ca Stats"),
            ],
            [
                KeyboardButton("\U0001f4e2 Broadcast"),
                KeyboardButton("\U0001f465 User Lookup"),
            ],
            [
                KeyboardButton("\U0001f504 Bot ON/OFF"),
                KeyboardButton("\U0001f4ac Chat ON/OFF"),
            ],
            [KeyboardButton("\U0001f3d9\ufe0f Main Menu")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Rug Munch Admin console",
    )


def admin_confirm_kb(action: str, payload: str = "") -> InlineKeyboardMarkup:
    """Generic confirm/cancel keyboard for destructive admin actions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\u2705 Confirm", callback_data=f"admin_confirm_{action}"),
                InlineKeyboardButton("\u274c Cancel", callback_data="admin_cancel"),
            ]
        ]
    )


def scan_actions_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Post-scan action row (v6): 📊 View Chart · 🗺️ View RugMap · 📤 Share · 🔍 Deep Scan."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f4c8 View Chart", callback_data=f"chart_{address}_{chain}"),
                InlineKeyboardButton("\U0001f5fa\ufe0f View RugMap", callback_data=f"rugmap_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f4e4 Share", url=_share_token_url(address, chain)),
                InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f6f0\ufe0f Monitor (250\u2605)", callback_data=f"monitor_{address}_{chain}"
                ),
                InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main"),
            ],
        ]
    )


def scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Post-scan action keyboard (v7 — decluttered 2026-08-16).

    Reference links (GMGN/DexScreener/DexTools/RugCharts/RugMaps) render as
    tappable text in the report footer (``footer_links``) — no duplicate
    link-button rows here. What remains are ACTION buttons: one tap runs a
    real flow (holders/research/wallet/TA/alert/watch/deep/share).
    """
    rows = [
        [web_scan_button(address, chain)],
        [
            InlineKeyboardButton("\U0001f465 Holders", callback_data=f"holders_{address}_{chain}"),
            InlineKeyboardButton("\U0001f50d Research", callback_data=f"research_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("\U0001f9db Wallet Check", callback_data=f"wallet_{address}_{chain}"),
            InlineKeyboardButton("\U0001f4ca TA", callback_data=f"ta_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("\U0001f514 Price Alert", callback_data=f"alert_{address}_{chain}"),
            InlineKeyboardButton("\u2b50 Watch", callback_data=f"watch_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
            InlineKeyboardButton("\U0001f4e4 Share", callback_data=f"share_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton("\U0001f6f0\ufe0f Monitor (250\u2605)", callback_data=f"monitor_{address}_{chain}"),
        ],
        [InlineKeyboardButton("\u2b50 Unlock Premium Features", callback_data="menu_pricing")],
        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(rows)


def pricing_kb() -> InlineKeyboardMarkup:
    """Premium pricing buttons — Stars-based (no fake $/mo framing).

    Tiers are priced in Telegram Stars (their real ``sub_`` invoice price,
    ``price_stars``); the working on-demand premium features (Monitor / Deep
    Scan) are promoted on top and reached from scan results + /monitor.
    """
    rows = [
        [
            InlineKeyboardButton(
                "\U0001f6f0\ufe0f Monitor (250\u2605/30d) \u2014 /monitor <addr>", callback_data="menu_monitor_howto"
            )
        ],
        [InlineKeyboardButton("\u2b50 Buy Stars", callback_data="menu_buystars")],
    ]
    for key in ["scout", "hunter", "pro"]:
        t = TIERS[key]
        rows.append(
            [
                InlineKeyboardButton(
                    f"{t['emoji']} {t['name']} - {t['price_stars']:,}\u2605/30d",
                    callback_data=f"sub_{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("\U0001f310 Compare Plans on Website", url=f"{WEBSITE_URL}/pricing")])
    rows.append([InlineKeyboardButton("\U0001f4e7 Enterprise", url=f"mailto:{SUPPORT_EMAIL}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def topup_kb() -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton("\U0001f52c Scan Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "scans":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\U0001f916 AI Message Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "ai_msgs":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\u2b50 Buy Stars", callback_data="menu_buystars")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def buy_stars_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, pack in BUY_STARS_PACKS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                    callback_data=f"buystars_{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def scamschool_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, topic in SCAM_SCHOOL_TOPICS.items():
        rows.append([InlineKeyboardButton(topic["title"], callback_data=f"scam_{key}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")]])


def social_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f310 Website", url=WEBSITE_URL),
                InlineKeyboardButton("\U0001d54f Twitter", url=TWITTER_URL),
            ],
            [
                InlineKeyboardButton("\U0001f4e2 Alerts", url=TELEGRAM_CHANNEL),
                InlineKeyboardButton("\U0001f4ac Chat", url=TELEGRAM_GROUP),
            ],
        ]
    )


def paywall_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\u2b50 View Plans", callback_data="menu_pricing")],
            [InlineKeyboardButton("\U0001f50b Buy Top-Up", callback_data="menu_topup")],
            [InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )


def holders_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}"),
                InlineKeyboardButton("\U0001f9db Wallet", callback_data=f"wallet_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f514 Set Alert", callback_data=f"alert_{address}_{chain}"),
                InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def trace_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton("\U0001f9db Wallet Analysis", callback_data=f"wallet_{address}_{chain}"),
                InlineKeyboardButton("\U0001f50d Scan Token", callback_data=f"scan_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
                InlineKeyboardButton("\U0001f440 Watch", callback_data=f"watch_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def investigate_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f310 Full Report", url=f"{WEBSITE_URL}/markets/token/{address}")],
            [
                InlineKeyboardButton("\U0001f9db Wallet", callback_data=f"wallet_{address}_{chain}"),
                InlineKeyboardButton("👥 Holders", callback_data=f"holders_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f50d Scan", callback_data=f"scan_{address}_{chain}"),
                InlineKeyboardButton("🔍 Trace", callback_data=f"trace_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def _share_token_url(address: str, chain: str) -> str:
    """t.me/share/url deep link advertising a scanned token (v6)."""
    from urllib.parse import quote

    return (
        "https://t.me/share/url?url="
        + quote(f"{WEBSITE_URL}/scanner?token={address}&chain={chain}")
        + "&text="
        + quote("Scanned with Rug Munch — check this token before you buy!")
    )


def deep_scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Extended keyboard shown after scan results with all premium actions."""
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton("👥 Holders", callback_data=f"holders_{address}_{chain}"),
                InlineKeyboardButton("🔍 Trace", callback_data=f"trace_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f50d Research", callback_data=f"research_{address}_{chain}"),
                InlineKeyboardButton("\U0001f4c8 Sentiment", callback_data=f"sentiment_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f9db Wallet Check", callback_data=f"wallet_{address}_{chain}"),
                InlineKeyboardButton("\U0001f4ca TA", callback_data=f"ta_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f514 Set Alert", callback_data=f"alert_{address}_{chain}"),
                InlineKeyboardButton("\u2b50 Watch", callback_data=f"watch_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f4c8 Chart", callback_data=f"chart_{address}_{chain}"),
                InlineKeyboardButton("\U0001f5fa\ufe0f RugMap", callback_data=f"rugmap_{address}_{chain}"),
                InlineKeyboardButton("\U0001f4e4 Share", url=_share_token_url(address, chain)),
            ],
            [
                InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
                InlineKeyboardButton("\U0001f575\ufe0f Investigate", callback_data=f"investigate_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def upgrade_nudge_kb(current_tier: str) -> InlineKeyboardMarkup:
    """Keyboard shown when user hits usage limits."""
    buttons = []
    if current_tier == "free":
        buttons.append([InlineKeyboardButton("🔍 Scout - 2,999★/30d", callback_data="sub_scout")])
        buttons.append([InlineKeyboardButton("🎯 Hunter - 4,999★/30d", callback_data="sub_hunter")])
    elif current_tier == "scout":
        buttons.append([InlineKeyboardButton("🎯 Hunter - 4,999★/30d", callback_data="sub_hunter")])
        buttons.append([InlineKeyboardButton("👑 Pro - 9,999★/30d", callback_data="sub_pro")])
    elif current_tier == "hunter":
        buttons.append([InlineKeyboardButton("👑 Pro - 9,999★/30d", callback_data="sub_pro")])
    buttons.append([InlineKeyboardButton("💳 View All Plans", callback_data="menu_pricing")])
    buttons.append([InlineKeyboardButton("🔄 Buy Top-Up", callback_data="menu_topup")])
    buttons.append([InlineKeyboardButton("🔙 Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)


def api_kb(has_token: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for /api command."""
    buttons = [
        [InlineKeyboardButton("📖 API Documentation", url=f"{WEBSITE_URL}/docs")],
    ]
    if has_token:
        buttons.append([InlineKeyboardButton("🔄 Revoke Token", callback_data="api_revoke")])
    buttons.append([InlineKeyboardButton("🔙 Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)


# ══════════════════════════════════════════════
# v11 — ENGAGEMENT (additive, 2026-08-14)
# First-run quick-start + returning-user daily hook.
# Every callback reuses an EXISTING surface (run_scan / run_trending /
# run_market / menu_cat_help) — zero callback-handler changes needed.
# ══════════════════════════════════════════════


def quick_start_kb() -> InlineKeyboardMarkup:
    """First-run quick-start: the 3 core actions + a "More" button → /menu.

    Scan / Price / Ask AI mirror the example actions in the onboarding
    welcome message; the final "More" row opens the full /menu capability
    atlas via the ``menu_full`` callback (renders the existing ``MENU_TEXT``).
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f50d Scan", callback_data="run_scan"),
                InlineKeyboardButton("\U0001f4b0 Price", callback_data="run_price"),
            ],
            [InlineKeyboardButton("\U0001f4ac Ask AI", callback_data="run_chat")],
            [InlineKeyboardButton("\U0001f4d6 More \u25be", callback_data="menu_full")],
        ]
    )


def daily_hook_kb() -> InlineKeyboardMarkup:
    """Returning-user daily hook: one-tap "what's hot right now" + market pulse.

    A lightweight daily-digest quick action (button, not a scheduler) that
    triggers the existing trending / market tools for instant value on return.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f525 What's hot right now", callback_data="run_trending"),
                InlineKeyboardButton("\U0001f4ca Market pulse", callback_data="run_market"),
            ]
        ]
    )


def merge_menu_kb(main_kb: InlineKeyboardMarkup, extra_kb: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Append ``extra_kb`` rows to ``main_kb`` (additive merge, no mutation).

    Keeps existing buttons intact and just adds the engagement rows on top —
    used by /start to attach the daily hook to the welcome-menu keyboard.
    """
    rows = [list(row) for row in main_kb.inline_keyboard]
    rows.extend(list(row) for row in extra_kb.inline_keyboard)
    return InlineKeyboardMarkup(rows)
