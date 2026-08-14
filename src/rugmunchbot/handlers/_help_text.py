#!/usr/bin/env python3
"""
RugMunchBot -- /help reference text & rendering helpers.

Extracted from ``handlers.scanning.py`` (degodding 2026-08-14) — pure text
constants and pure render functions only. No network, DB, or Telegram API
access: rendering the help reference is deterministic given ``config``.

Everything here is re-exported by ``handlers.scanning`` so existing
importers of ``scanning.HELP_TEXT`` / ``_HELP_SECTIONS`` / ``CHANNEL_LABELS``
keep working unchanged (same objects — ``HELP_TEXT`` is built once at import
time, exactly as before).

"""

from __future__ import annotations

import html
import os

from rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL
from rugmunchbot.formatters import (
    BETA_BANNER,
    about_text,
    footer_links,
    sep,
    thin_sep,
)

# ══════════════════════════════════════════════
# /help reference — grouped, scannable (v10)
# Six primary categories (Scan & Security / Wallet & Tracing /
# Market & Analysis / News & Alerts / Chat & Learn / Premium) mirror the
# composer-menu order and the inline keyboard sections. Account & Extras
# keeps the remaining self-service + group-admin commands reachable.
# Owner/admin commands are documented in the admin panel, not here.
# Rendered with HTML parse mode — every <code> tag is balanced.
# ══════════════════════════════════════════════
_HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Scan & Security",
        [
            ("scan", "<code>address</code> - quick security audit"),
            ("deepscan", "<code>address</code> - full forensic scan"),
            ("sentinelscan", "<code>address</code> - AEGIS 9-module scan"),
            ("rugcheck", "<code>address</code> - red flag checklist"),
            ("deployer", "<code>address</code> - deployer fingerprint"),
            ("hype", "<code>address</code> - 0-100 hype score"),
            ("compare", "<code>a</code> <code>b</code> - side-by-side compare"),
            ("investigate", "<code>address</code> - deep report (Pro)"),
            ("rugmap", "<code>address</code> - bubble map image"),
            ("report", "<code>address</code> - report a scam"),
        ],
    ),
    (
        "Wallet & Tracing",
        [
            ("wallet", "<code>address</code> - wallet forensics"),
            ("portfolio", "<code>address</code> - wallet holdings"),
            ("simulate", "<code>address</code> - buy/sell simulation"),
            ("trace", "<code>address</code> - cross-chain fund trace"),
            ("flow", "<code>address</code> - money flow tracking"),
            ("dumps", "<code>address</code> - dump pattern detector"),
            ("smartmoney", "<code>address</code> - smart money detection"),
        ],
    ),
    (
        "Market & Analysis",
        [
            ("market", "- market overview (BTC/ETH, F&G)"),
            ("price", "<code>address</code> - token price"),
            ("quick", "<code>symbol</code> - instant price check"),
            ("chart", "<code>address</code> - chart image (OHLCV)"),
            ("ta", "<code>address</code> - technical analysis"),
            ("dex", "<code>address</code> - DEX pairs & liquidity"),
            ("trending", "[chain] - hot tokens right now"),
            ("movers", "- biggest 24h gainers & losers"),
            ("launches", "[chain] - new launch feed"),
            ("whales", "- whale movement alerts"),
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
        "News & Alerts",
        [
            ("news", "- latest crypto news"),
            ("events", "- news events & price impact"),
            ("alerts", "- manage price & activity alerts"),
            ("alert", "ABOVE/BELOW/CHANGE_5 <code>addr</code> <code>price</code>"),
            ("watch", "<code>address</code> - add to watchlist"),
            ("unwatch", "<code>address</code> - remove from watchlist"),
            ("watchlist", "- view your watchlist"),
            ("whale", "<code>address</code> - whale alerts"),
            ("webhook", "- custom webhook notifications (Pro)"),
        ],
    ),
    (
        "Chat & Learn",
        [
            ("chat", "<code>question</code> - freestyle AI chat"),
            ("chat reset", "- clear conversation context"),
            ("ask", "<code>question</code> - deep RAG answer"),
            ("research", "<code>query</code> - deep research (Hunter+)"),
            ("faq", "- how the bot works, free? accuracy"),
            ("scamschool", "- learn the scam playbook"),
        ],
    ),
    (
        "Premium",
        [
            ("honeypot", "<code>address</code> - honeypot detector"),
            ("impersonation", "<code>address</code> - fake token check"),
            ("mev", "<code>address</code> - MEV risk & safe swaps"),
            ("lpcheck", "<code>address</code> - LP health check"),
            ("lphealth", "<code>address</code> - liquidity pool health"),
            ("deathclock", "<code>address</code> - survival prediction"),
            ("laundry", "<code>address</code> - laundering detector"),
            ("cluster", "<code>address</code> - wallet clustering"),
        ],
    ),
    (
        "Account & Extras",
        [
            ("account", "- dashboard & usage stats"),
            ("pricing", "- subscription plans"),
            ("topup", "- buy extra scans"),
            ("buystars", "- buy Telegram Stars"),
            ("refer", "- invite friends, earn scans"),
            ("api", "- API access token (Pro)"),
            ("banuser", "<code>user_id</code> - group ban (admins)"),
            ("mygroup", "- your creator coin group"),
            ("groupinfo", "- group stats (admin)"),
            ("voicechat", "- manage voice chat (admin)"),
        ],
    ),
]

_HELP_EMOJI: dict[str, str] = {
    "Scan & Security": "\U0001f6e1\ufe0f",
    "Wallet & Tracing": "\U0001f50d",
    "Market & Analysis": "\U0001f4c8",
    "News & Alerts": "\U0001f4f0",
    "Chat & Learn": "\U0001f4ac",
    "Premium": "\U0001f451",
    "Account & Extras": "\U0001f4b3",
}

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
    ``alpha`` uses the ``BOT_TELEGRAM_ALPHA`` env handle when the operator
    sets one (the live bot hardcoded a private handle here — scrubbed).
    Remaining channels fall back to the ``https://t.me/c/<id>`` deep link
    derived from their numeric chat id (the ``-100`` supergroup prefix is
    stripped). Returns ``None`` when no link can be resolved — unresolvable
    channels are simply omitted from the rendered section.
    """
    if key == "alerts" and TELEGRAM_CHANNEL:
        return TELEGRAM_CHANNEL
    if key == "alpha":
        alpha_handle = os.getenv("BOT_TELEGRAM_ALPHA", "").strip()
        if alpha_handle:
            return alpha_handle
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
    """Render the full /help reference (HTML) from _HELP_SECTIONS."""
    s = sep()
    d = thin_sep()
    parts = [f"\U0001f4d6 <b>Rug Munch Intelligence - Command Guide</b>\n{s}"]
    default_emoji = "\U0001f4d6"
    for header, cmds in _HELP_SECTIONS:
        emoji = _HELP_EMOJI.get(header, default_emoji)
        parts.append(f"\n{emoji} <b>{header}</b>\n{d}")
        for cmd, desc in cmds:
            parts.append(f"/{cmd} {desc}")
    channels = _channels_section()
    if channels:
        parts.append(f"\n{channels}")
    parts.append(
        f"\n{BETA_BANNER}\n\n"
        f"\U0001f4a1 <b>PRO TIPS</b>\n{d}\n"
        "\u2022 Paste any <code>0x...</code> address to auto-scan\n"
        "\u2022 Type <code>$PEPE</code> for instant price check\n"
        "\u2022 After any scan, tap Chart / RugMap / Share / Deep Scan\n"
        "\u2022 Use /chat for AI questions about tokens, wallets, DeFi, coding\n"
        "\u2022 Use inline mode: <code>@RugMunchBot address</code> in any chat\n"
        "\u2022 See a scam? /report <code>address</code> keeps us all safer\n"
        "\u2022 Upgrade for wallet forensics, fund tracing & more"
    )
    parts.append(f"\n{about_text()}")
    parts.append(footer_links())
    return "\n".join(parts)


HELP_TEXT: str = _build_help_text()
