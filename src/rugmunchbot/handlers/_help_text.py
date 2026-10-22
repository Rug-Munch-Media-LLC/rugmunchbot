#!/usr/bin/env python3
"""RugMunchBot -- /help & /more reference text, generated from the registry.

2026-10-23 rewrite: this module no longer hand-maintains command lists. Both
surfaces are BUILT from the same data the bot registers:

  * ``/help``  (``HELP_TEXT``)  — guided tour. One line per ADVERTISED command
    (``bot/_commands._COMMAND_SECTIONS``, ≤ 20) grouped by intent, then the
    pointer to ``/more``. A command cannot be advertised without appearing
    here, and a typo cannot invent a command: both lists come from the same
    registry object.
  * ``/more``  (``MENU_TEXT``) — the catalog index: every group, its command
    count, examples, and the drill-down syntax (``/more scans``). Group bodies
    are rendered by ``handlers/_more.py`` from ``bot/_catalog.py``.

Pure text + pure render functions: no network, DB, or Telegram API. Rendering
is deterministic given ``config`` (channels) and the registry constants, so
the tests assert help/registry parity without a live bot.
"""

from __future__ import annotations

import html

from rugmunchbot.command_catalog import CATALOG
from rugmunchbot.command_menu import ADVERTISED_SECTIONS
from rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL
from rugmunchbot.formatters import footer_links, sep, thin_sep

#: One-line plain-English pitch per advertised group. Copy lives here (it is
#: presentation), never the command lists (those are generated).
_GROUP_PITCHES: dict[str, str] = {
    "Start here": "The 30-second tour, the catalogue, and what it costs.",
    "Scan & safety": "Audit a token before you buy — honeypots, bundles, holders, fund flows.",
    "Markets": "Prices, charts, and what is actually moving right now.",
    "Intel": "Written intelligence, deep research, alerts and your own book.",
    "Account": "Your tier, your usage, and a way to report a scam.",
}

_GROUP_EMOJI: dict[str, str] = {
    "Start here": "\U0001f680",
    "Scan & safety": "\U0001f6e1\ufe0f",
    "Markets": "\U0001f4c8",
    "Intel": "\U0001f4f0",
    "Account": "\U0001f464",
}

#: (header, plain-English description, examples) per advertised group —
#: derived from the advertised menu so /help always lists what the menu shows.
_HELP_SECTIONS: list[tuple[str, str, str]] = [
    (
        header,
        _GROUP_PITCHES.get(header, ""),
        " \u00b7 ".join(f"/{cmd}" for cmd, _desc in cmds),
    )
    for header, cmds in ADVERTISED_SECTIONS
]

_HELP_EMOJI: dict[str, str] = {h: _GROUP_EMOJI.get(h, "\U0001f4d6") for h, _d, _e in _HELP_SECTIONS}

#: Full catalog grouped by intent — the /more atlas (derived from CATALOG).
_MENU_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    (title, [(name, desc) for name, desc in items]) for _slug, title, _emoji, items in CATALOG
]

_MENU_EMOJI: dict[str, str] = {title: emoji for _slug, title, emoji, _items in CATALOG}

# ══════════════════════════════════════════════
# Project & Open Source links (public mirrors across all platforms).
# ══════════════════════════════════════════════

CODEBERG_ORG: str = "https://codeberg.org/cryptorugmunch"
FORGEJO_ORG: str = "https://git.rugmunch.io/RugMunchMedia"
GITLAB_ORG: str = "https://gitlab.com/cryptorugmunch"
RADICLE_SEED: str = "https://git.cryptorugmunch.com"

OPEN_SOURCE_REPOS: tuple[tuple[str, str], ...] = (
    ("bot", f"{CODEBERG_ORG}/rugmunchbot"),
    ("scraper", f"{CODEBERG_ORG}/pryscraper"),
    ("frontend", f"{CODEBERG_ORG}/rmi-frontend"),
    ("rag", "https://codeberg.org/RugMunchMedia/rugbrain"),
    ("indexer", f"{CODEBERG_ORG}/chain-indexer"),
    ("wallet", f"{CODEBERG_ORG}/walletpress"),
)


def project_links_section() -> str:
    """Compact 'Project & Open Source' block (HTML-safe, three lines)."""
    repos = " \u00b7 ".join(
        f'<a href="{url}">{html.escape(label, quote=True)}</a>' for label, url in OPEN_SOURCE_REPOS
    )
    return (
        "\U0001f517 <b>Project &amp; Open Source</b>\n"
        '\U0001f310 <a href="https://rugmunch.io">rugmunch.io</a> \u2014 crypto intelligence\n'
        f"\U0001f419 {repos}"
    )


def build_in_public_section() -> str:
    """Full 'Built in Public' block with all four repo mirrors (HTML-safe)."""
    repos = " \u00b7 ".join(
        f'<a href="{url}">{html.escape(label, quote=True)}</a>' for label, url in OPEN_SOURCE_REPOS
    )
    return (
        "\U0001f6e0\ufe0f <b>Built in Public</b>\n"
        "Every line of code ships open source \u2014 see what's coming next, "
        "report issues, or fork the stack.\n\n"
        f"\U0001f419 <b>Repos</b>\n{repos}\n\n"
        "\U0001f4e6 <b>Mirrors</b>\n"
        f'\U0001f310 <a href="{CODEBERG_ORG}">Codeberg</a>'
        f' \u00b7 <a href="{FORGEJO_ORG}">Forgejo</a>'
        f' \u00b7 <a href="{GITLAB_ORG}">GitLab</a>'
        f' \u00b7 <a href="{RADICLE_SEED}">Radicle</a>'
    )


# ── Community channels surfaced in /help (config.CHANNELS-backed) ──────────

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
    back to the ``https://t.me/c/<id>`` deep link derived from their numeric
    chat id. Returns ``None`` when no link can be resolved.
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
    """Render /help: what the bot is, then every ADVERTISED command once."""
    advertised = sum(len(cmds) for _h, cmds in ADVERTISED_SECTIONS)
    parts = [
        f"\U0001f4d6 <b>Rug Munch Bot \u2014 How It Works</b>\n{sep()}",
        "\U0001f575\ufe0f <b>Rug Munch Intelligence Analyst</b> \u2014 scan any "
        "token for rug, honeypot &amp; bundle risk, then get AI-written "
        "verdicts, prices, charts, news and wallet intel \u2014 before you buy.\n",
        f"\U0001f680 <b>WHAT YOU CAN DO</b> \u2014 {advertised} commands, "
        f"the rest behind <b>/more</b>\n{thin_sep()}",
    ]
    for header, cmds in ADVERTISED_SECTIONS:
        emoji = _GROUP_EMOJI.get(header, "\U0001f4d6")
        pitch = _GROUP_PITCHES.get(header, "")
        parts.append(f"\n{emoji} <b>{header}</b>\n{pitch}")
        parts.append(" \u00b7 ".join(f"/{cmd}" for cmd, _desc in cmds))
    parts.append(
        "\n\U0001f5fa\ufe0f <b>/more</b> \u2014 the full catalogue, grouped "
        "(<code>/more scans</code>, <code>/more markets</code>, ...)\n"
        "\U0001f4ac <b>No command needed</b> \u2014 paste an address and I scan "
        "it, or type <code>$SOL</code> for a price\n"
        "\U0001f510 <b>Honest pricing</b> \u2014 /plans says exactly what is "
        "free today and what Pro will cost"
    )
    channels = _channels_section()
    if channels:
        parts.append(f"\n{channels}")
    parts.append(
        f"\n\U0001f4a1 <b>PRO TIPS</b>\n{thin_sep()}\n"
        "\u2022 Paste any <code>0x...</code> address \u2014 I auto-scan it\n"
        "\u2022 In groups, <b>@mention or reply to me</b> \u2014 private DMs "
        "always answer\n"
        "\u2022 See a scam? /report <code>address</code> keeps us all safer"
    )
    parts.append(footer_links())
    return "\n".join(parts)


def _build_menu_text() -> str:
    """Render /more: the catalog INDEX (groups, counts, examples, drill-down).

    Deliberately an index, not 95 rows: one Telegram message holds ~4096
    chars and a 95-row wall is unreadable on a phone. Each group is one line
    plus its top examples; ``/more <slug>`` renders the full group.
    """
    parts = [
        f"\U0001f5fa\ufe0f <b>Rug Munch \u2014 All Commands</b>\n{sep()}",
        f"{sum(len(items) for *_x, items in CATALOG)} commands that work today. "
        "The menu shows the 20 you will actually use.\n",
    ]
    for slug, title, emoji, items in CATALOG:
        examples = " \u00b7 ".join(f"/{name}" for name, _desc in items[:3])
        parts.append(
            f"\n{emoji} <b>{title}</b> \u2014 {len(items)} commands\n"
            f"\u2022 {examples}\n"
            f"\u21b3 <code>/more {slug}</code>"
        )
    parts.append(
        f"\n{thin_sep()}\n\U0001f4d6 Guided tour: /help \u00b7 \U0001f510 What it costs: /plans"
    )
    return "\n".join(parts)


HELP_TEXT: str = _build_help_text()
MENU_TEXT: str = _build_menu_text()
