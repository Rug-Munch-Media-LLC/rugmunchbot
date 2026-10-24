"""Canonical Telegram styling rules — the ONE source of layout truth.

Every formatter, channel poster and alert worker that emits Telegram HTML
consumes these constants through :mod:`rugmunchbot.content_style`. Do not
re-declare a limit, a bullet or a gap anywhere else: a second copy is the
exact double-config defect this module removes (five independent 3900-char
splitters and three different body caps existed before 2026-10-24).

Rules are data, not code: env overrides exist for operator tuning only and
are read once at import.
"""

from __future__ import annotations

import os
from typing import Final

# ── Length ────────────────────────────────────────────────────────────────
#: Hard Telegram ceiling for one message (Bot API limit, never exceeded).
TELEGRAM_LIMIT: Final[int] = 4096
#: Budget we actually fill: hard limit minus formatting headroom, so a
#: re-wrap (entity expansion, appended CTA) can never trip the ceiling.
SAFE_LIMIT: Final[int] = int(os.getenv("RMI_TG_SAFE_LIMIT", "3900"))

# ── Paragraphs and bullets ────────────────────────────────────────────────
#: Paragraph separator: exactly one blank line, never two.
PARAGRAPH_GAP: Final[str] = "\n\n"
#: Canonical bullet glyph and its continuation indent.
BULLET: Final[str] = "\u2022 "
BULLET_INDENT: Final[str] = "  "
#: Bullet glyphs rewritten to :data:`BULLET` (markdown + unicode bullets).
BULLET_ALIASES: Final[tuple[str, ...]] = ("-", "*", "+", "\u00b7", "\u2013", "\u2014")

# ── Headings ──────────────────────────────────────────────────────────────
#: Ruled-banner heading style: ``<b>━━━ TITLE ━━━</b>``.
HEADING_RULE: Final[str] = "\u2501\u2501\u2501"
#: Spec law: a standalone bold headline line is at most this many chars.
MAX_HEADLINE_CHARS: Final[int] = int(os.getenv("RMI_FORMAT_MAX_HEADLINE", "80"))

# ── Emoji ─────────────────────────────────────────────────────────────────
#: No emoji may repeat more than this many times in one post.
MAX_SAME_EMOJI: Final[int] = int(os.getenv("RMI_FORMAT_MAX_SAME_EMOJI", "2"))
#: Allowlist: emoji with an established meaning on our surfaces. Anything
#: outside it is noise in a channel post (scrubbed only when a caller opts in).
EMOJI_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "\u2192",
        "\u26a0",
        "\u26a1",
        "\u26aa",
        "\u26d4",
        "\u2705",
        "\u274c",
        "\u2b1c",
        "\u2b50",
        "\u2605",
        "\u2713",
        "\u2717",
        "\u2139",
        "\U0001f3af",
        "\U0001f3e6",
        "\U0001f40b",
        "\U0001f465",
        "\U0001f4a1",
        "\U0001f4a7",
        "\U0001f4b0",
        "\U0001f4c8",
        "\U0001f4c9",
        "\U0001f4ca",
        "\U0001f4d6",
        "\U0001f4e1",
        "\U0001f4f0",
        "\U0001f504",
        "\U0001f517",
        "\U0001f50d",
        "\U0001f52c",
        "\U0001f534",
        "\U0001f550",
        "\U0001f6a8",
        "\U0001f7e0",
        "\U0001f7e1",
        "\U0001f7e2",
        "\U0001f916",
        "\U0001f9e0",
        "\U0001fa99",
        "\U0001f6e1",
        "\u26d3",
    }
)

# ── Links ─────────────────────────────────────────────────────────────────
#: Anchor label budget; longer labels are clipped at a word boundary.
LINK_LABEL_MAX: Final[int] = 60
#: A URL appears at most this many times per post (hard rule).
MAX_LINK_REPEATS: Final[int] = 1

# ── Footer / CTA ──────────────────────────────────────────────────────────
#: Canonical bot CTA row — every channel post closes with one product line.
BOT_CTA: Final[str] = (
    '\U0001f916 <a href="https://t.me/RugMunchBot">@RugMunchBot</a> \u2014 scan any token, anytime'
)
#: Canonical site CTA row (used when a lane has no story slug).
SITE_CTA: Final[str] = (
    '\U0001f310 <a href="https://rugmunch.io">rugmunch.io</a> \u2014 scanner, charts &amp; maps'
)
