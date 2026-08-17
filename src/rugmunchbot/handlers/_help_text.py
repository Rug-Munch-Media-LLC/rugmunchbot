#!/usr/bin/env python3
"""
RugMunchBot -- /help & /menu reference text & rendering helpers.

Extracted from ``handlers.scanning.py`` (degodding 2026-08-14) — pure text
constants and pure render functions only. No network, DB, or Telegram API
access: rendering the help reference is deterministic given ``config``.

Everything here is re-exported by ``handlers.scanning`` so existing
importers of ``scanning.HELP_TEXT`` / ``_HELP_SECTIONS`` / ``CHANNEL_LABELS``
keep working unchanged (same objects — ``HELP_TEXT`` is built once at import
time, exactly as before).

v14 (2026-08-16): rebuilt /help as a first-run EXPLAINER, not a command soup:

  * ``/help``  (``HELP_TEXT``) — "how the app works" onboarding: what the bot
    is (the 3-line pitch), then five what-you-can-do groups — Scan & Safety /
    Prices & Charts / Portfolio / News & Intel / Ask AI — each with a one-line
    plain-English description and ONE example. No command-name soup. The
    community-channels block and PRO TIPS are kept.

  * ``/menu``  (``MENU_TEXT``) — unchanged capability atlas grouped by intent
    (Scan & Safety / Prices & Charts / Portfolio & Wallets / News & Intel /
    Education / Account & Community). Every command listed is a REAL
    registered command (verified against ``bot.py``); each line carries a
    one-line description + example usage.

All command names below were cross-checked against the actual registrations
in ``bot.py`` (``_reg(app, ...)`` + ``register_market/prediction/defi/
trace_commands``). Owner-only ``/admin_*`` commands are documented in the
admin panel, not here. There is intentionally NO ``/me`` command anywhere
(not registered) — it is not listed.
"""

from __future__ import annotations

import html

from app.domains.telegram.rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    sep,
    thin_sep,
)

# ══════════════════════════════════════════════
# /help — first-run EXPLAINER (v14)
# Five what-you-can-do groups, each a (header, one-line plain-English
# description, ONE example) triple. This is an on-ramp, NOT a command
# dump — the full atlas lives in ``_MENU_SECTIONS`` → /menu. Every command
# referenced here is a REAL registered command (verified against bot.py).
# Rendered with HTML parse mode — every <code> tag is balanced.
# ══════════════════════════════════════════════
_HELP_SECTIONS: list[tuple[str, str, str]] = [
    (
        "Scan & Safety",
        "Audit any token for rug pulls, honeypots and bundle risk \u2014 before you buy.",
        "/scan <code>&lt;address&gt;</code>",
    ),
    (
        "Prices & Charts",
        "Live prices, chart images and technical signals for any token.",
        "<code>$SOL</code>",
    ),
    (
        "Portfolio",
        "Wallet holdings, forensics and watchlists \u2014 know what you own.",
        "/wallet <code>&lt;address&gt;</code>",
    ),
    (
        "News & Intel",
        "Headlines, market trends and alerts \u2014 never miss a move.",
        "/news",
    ),
    (
        "Ask AI",
        "Freestyle AI answers with deep research context \u2014 no commands needed.",
        "/chat <code>&lt;question&gt;</code>",
    ),
]

_HELP_EMOJI: dict[str, str] = {
    "Scan & Safety": "\U0001f6e1\ufe0f",
    "Prices & Charts": "\U0001f4c8",
    "Portfolio": "\U0001f45b",
    "News & Intel": "\U0001f4f0",
    "Ask AI": "\U0001f4ac",
}

# ══════════════════════════════════════════════
# /menu — capability atlas grouped by intent (v13)
# One entry per REAL registered command (verified against bot.py). The
# ``desc`` carries a one-line description + a concrete example usage.
# ══════════════════════════════════════════════
_MENU_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Scan & Safety",
        [
            ("scan", "<code>address</code> - quick security audit"),
            ("deepscan", "<code>address</code> - ML deep scan (fraud-GNN, ABI)"),
            ("sentinelscan", "<code>address</code> - AEGIS 9-module scan"),
            ("rugcheck", "<code>address</code> - red-flag checklist"),
            ("deployer", "<code>address</code> - deployer fingerprint"),
            ("hype", "<code>address</code> - 0-100 hype score"),
            ("compare", "<code>a</code> <code>b</code> - side-by-side compare"),
            ("investigate", "<code>target</code> - deep report (Pro)"),
            ("rugmap", "<code>address</code> - bubble-map image"),
            ("report", "<code>address</code> - report a scam"),
            ("honeypot", "<code>address</code> - honeypot detector"),
            ("impersonation", "<code>address</code> - fake token check"),
            ("mev", "<code>address</code> - MEV risk & safe swaps"),
            ("lpcheck", "<code>address</code> - LP health check (/lphealth = alias)"),
            ("deathclock", "<code>address</code> - survival prediction"),
            ("laundry", "<code>address</code> - laundering detector"),
            ("cluster", "<code>address</code> - wallet clustering"),
        ],
    ),
    (
        "Prices & Charts",
        [
            ("<code>$SYMBOL</code>", "- instant price (e.g. $SOL or $BONK)"),
            ("price", "<code>address</code> - token price"),
            ("quick", "<code>symbol</code> - instant price check"),
            ("chart", "<code>address</code> - chart image (OHLCV)"),
            ("ta", "<code>address</code> - technical analysis & signals"),
            ("dex", "<code>address</code> - DEX pairs & liquidity"),
            ("market", "- market overview (BTC/ETH, F&G)"),
            ("trending", "[chain] - hot tokens right now"),
            ("movers", "- biggest 24h gainers & losers"),
            ("launches", "[chain] - new launch feed"),
            ("whales", "- recent whale moves feed"),
            ("sentiment", "- fear/greed & indicators"),
            ("holders", "<code>address</code> - top holders (Scout+)"),
            ("yield", "- DeFi yield opportunities"),
            ("defi", "<code>protocol</code> - DeFi analytics"),
            ("polymarket", "- prediction markets"),
            ("election", "- election odds"),
            ("odds", "- sports & event odds"),
        ],
    ),
    (
        "Portfolio & Wallets",
        [
            ("wallet", "<code>address</code> - wallet forensics"),
            ("portfolio", "<code>address</code> - wallet holdings"),
            ("simulate", "<code>address</code> - buy/sell simulation"),
            ("smartmoney", "<code>address</code> - smart money detection"),
            ("trace", "<code>address</code> - cross-chain fund trace"),
            ("flow", "<code>address</code> - money flow tracking"),
            ("dumps", "<code>address</code> - dump pattern detector"),
            ("track", "<code>address</code> - wallet entity/sanction check"),
            ("watchlist", "- view your watchlist"),
            ("watch", "<code>address</code> - add to watchlist"),
            ("unwatch", "<code>address</code> - remove from watchlist"),
        ],
    ),
    (
        "News & Intel",
        [
            ("news", "- latest crypto news"),
            ("events", "- news events & price impact"),
            ("dune", "- curated on-chain intel (Dune queries)"),
            ("chat", "<code>question</code> - freestyle AI chat"),
            ("ask", "<code>question</code> - deep RAG answer"),
            ("research", "<code>query</code> - deep research (Hunter+)"),
            ("alerts", "- manage price & activity alerts"),
            ("alert", "ABOVE|BELOW <code>addr</code> <code>price</code>"),
            ("whale", "<code>address</code> - subscribe to whale alerts (/whales = feed)"),
            ("webhook", "- custom webhook alerts (Pro)"),
            ("api", "- API access token (Pro)"),
            ("osint", "<code>username</code> - find linked public accounts"),
            ("osintall", "<code>target</code> - run every OSINT module"),
            ("email", "<code>email</code> - breach & reputation check"),
            ("phone", "<code>phone</code> - carrier & region lookup"),
            ("face", "<code>photo</code> - face detection & reverse search"),
        ],
    ),
    (
        "Education",
        [
            ("scamschool", "- learn the scam playbook"),
            ("faq", "- how the bot works, is it free, accuracy"),
            ("start", "- welcome & quick start"),
            ("help", "- full command guide"),
            ("menu", "- this menu"),
        ],
    ),
    (
        "Account & Community",
        [
            ("account", "- dashboard & usage stats"),
            ("pricing", "- subscription plans"),
            ("topup", "- buy extra scans"),
            ("buystars", "- buy Stars"),
            ("refer", "- invite friends, earn scans"),
            ("mygroup", "- your creator coin group"),
            ("groupinfo", "- group stats (admin)"),
            ("voicechat", "- manage voice chat (admin)"),
            ("banuser", "<code>user_id</code> - group ban (admins)"),
        ],
    ),
]

_MENU_EMOJI: dict[str, str] = {
    "Scan & Safety": "\U0001f6e1\ufe0f",
    "Prices & Charts": "\U0001f4c8",
    "Portfolio & Wallets": "\U0001f45b",
    "News & Intel": "\U0001f4f0",
    "Education": "\U0001f4da",
    "Account & Community": "\U0001f464",
}

# ══════════════════════════════════════════════
# Project & Open Source links (public Codeberg mirrors only — never the
# internal forgejo/radicle remotes). Single source of truth for the compact
# "🔗 Project & Open Source" block shown in the /start welcome and the /menu
# atlas (reused by ``keyboards.category_help_kb``).
# ══════════════════════════════════════════════

#: Public Codeberg org page for the RugMunch open-source repos.
CODEBERG_ORG: str = "https://codeberg.org/cryptorugmunch"

#: The six public RMI open-source repos: (short label, public Codeberg URL).
OPEN_SOURCE_REPOS: tuple[tuple[str, str], ...] = (
    ("bot", f"{CODEBERG_ORG}/rugmunchbot"),
    ("scraper", f"{CODEBERG_ORG}/pryscraper"),
    ("frontend", f"{CODEBERG_ORG}/rmi-frontend"),
    ("rag", f"{CODEBERG_ORG}/rmi-rag"),
    ("indexer", f"{CODEBERG_ORG}/chain-indexer"),
    ("wallet", f"{CODEBERG_ORG}/walletpress"),
)


def project_links_section() -> str:
    """Compact '🔗 Project & Open Source' block for welcome/menu (HTML-safe).

    Three lines only — one section header, the website, one repo line with
    short labels — so it stays a small block, never a wall of links.
    """
    repos = " \u00b7 ".join(f'<a href="{url}">{html.escape(label, quote=True)}</a>' for label, url in OPEN_SOURCE_REPOS)
    return (
        "\U0001f517 <b>Project &amp; Open Source</b>\n"
        '\U0001f310 <a href="https://rugmunch.io">rugmunch.io</a> \u2014 crypto intelligence\n'
        f"\U0001f419 {repos}"
    )


# ── Community channels surfaced in /help (config.CHANNELS-backed) ──────────

#: Display labels for the community channels, keyed by config.CHANNELS keys.
CHANNEL_LABELS: dict[str, str] = {
    "main": "Main",
    "news": "News",
    "alerts": "Alerts",
    "alpha": "Alpha",
    "scans": "Scans",
    "whale": "Whale",
}


def _channel_url(key: str, chat_id: object) -> str | None:
    """Resolve one ``config.CHANNELS`` entry to a Telegram link.

    ``alerts`` uses the verified public handle (``TELEGRAM_CHANNEL``);
    ``alpha`` is the ``@rmialpha`` public handle. Remaining channels fall
    back to the ``https://t.me/c/<id>`` deep link derived from their
    numeric chat id (the ``-100`` supergroup prefix is stripped). Returns
    ``None`` when no link can be resolved — unresolvable channels are
    simply omitted from the rendered section.
    """
    if key == "alerts" and TELEGRAM_CHANNEL:
        return TELEGRAM_CHANNEL
    if key == "alpha":
        return "https://t.me/rmialpha"
    numeric = str(chat_id or "").strip()
    if numeric.startswith("-100") and numeric[4:].isdigit():
        return f"https://t.me/c/{numeric[4:]}"
    return None


def _channels_section() -> str:
    """Render the community-channels block for /help (HTML-safe)."""
    entries: list[str] = []
    for key, chat_id in CHANNELS.items():
        url = _channel_url(key, chat_id)
        if url is None:
            continue
        label = CHANNEL_LABELS.get(key, key.capitalize())
        entries.append(f'\u2022 <a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
    if not entries:
        return ""
    return f"\U0001f4e2 <b>Channels</b>\n{thin_sep()}\n" + "\n".join(entries)


def _build_help_text() -> str:
    """Render the /help EXPLAINER (HTML) — "how the app works" onboarding.

    v14: what the bot is (the 3-line pitch), then five what-you-can-do
    groups — each a one-line plain-English description + ONE example. No
    command-name soup. The community-channels block and PRO TIPS follow,
    then the pointer to /menu for the full atlas. Stays well under
    Telegram's 4096-char message limit.
    """
    s = sep()
    d = thin_sep()
    parts = [
        f"\U0001f4d6 <b>Rug Munch Bot \u2014 How It Works</b>\n{s}",
        "\U0001f575\ufe0f <b>Rug Munch Intelligence Analyst</b> \u2014 scan any "
        "token for rug, honeypot &amp; bundle risk, then get AI-written "
        "verdicts, prices, charts, news and wallet intel \u2014 before you "
        "buy. Built by <b>Rug Munch Intelligence</b> \u2014 "
        "<b>Rug Munch Media LLC</b> (Wyoming).\n",
        f"\U0001f680 <b>WHAT YOU CAN DO</b>\n{d}",
    ]
    for header, desc, example in _HELP_SECTIONS:
        emoji = _HELP_EMOJI.get(header, "\U0001f4d6")
        parts.append(f"\n{emoji} <b>{header}</b>\n{desc}\n\U0001f449 {example}")
    parts.append(
        f"\n\U0001f4ac <b>WHAT YOU CAN ASK</b> (via /chat or /ask)\n{d}\n"
        "\u2022 \u201cIs this token safe?\u201d \u2014 paste an address with your question\n"
        "\u2022 \u201cWhy is BTC dumping?\u201d \u2014 market analysis\n"
        "\u2022 \u201cExplain pump &amp; dump\u201d \u2014 plain-English education\n"
    )
    channels = _channels_section()
    if channels:
        parts.append(f"\n{channels}")
    parts.append(
        f"\n\U0001f4a1 <b>PRO TIPS</b>\n{d}\n"
        "\u2022 Paste any <code>0x...</code> address \u2014 I auto-scan it\n"
        "\u2022 Type <code>$SOL</code> for an instant price check\n"
        "\u2022 In groups, <b>@mention or reply to me</b> \u2014 I stay quiet "
        "in group chats unless you call me (private DMs always answer)\n"
        "\u2022 See a scam? /report <code>address</code> keeps us all safer\n\n"
        "\U0001f5fa\ufe0f Full command atlas: /menu \u2014 grouped by category"
    )
    parts.append(footer_links())
    return "\n".join(parts)


def _build_menu_text() -> str:
    """Render the /menu capability atlas (HTML), grouped by intent.

    v13: five (plus Account & Community) intent groups, every line a REAL
    registered command with a one-line description + example usage. The
    quick-action inline keyboard is built in ``keyboards.menu_kb()``.
    """
    s = sep()
    d = thin_sep()
    parts = [
        f"\U0001f5fa\ufe0f <b>Rug Munch Bot \u2014 Command Menu</b>\n{s}",
        "Tap a button or type any command.\n",
    ]
    for header, cmds in _MENU_SECTIONS:
        emoji = _MENU_EMOJI.get(header, "\U0001f4d6")
        parts.append(f"\n{emoji} <b>{header}</b>\n{d}")
        for cmd, desc in cmds:
            if cmd.startswith("<code>"):
                parts.append(f"\u2022 {cmd} {desc}")
            else:
                parts.append(f"\u2022 /{cmd} {desc}")
    parts.append(f"\n{project_links_section()}")
    return "\n".join(parts)


HELP_TEXT: str = _build_help_text()
MENU_TEXT: str = _build_menu_text()
