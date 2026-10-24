"""Canonical Telegram content style — one renderer for every surface.

Public contract (see ``docs/CONTENT-STYLE-CONTRACT.md``):

* :func:`prepare_for_send` — normalize + HTML-safe split; the pre-send call.
* :func:`render_post` — the only post assembler (title, summary, key line,
  body, footer/CTA).
* :func:`render_daily_rug` — the Daily Rug envelope.
* :func:`normalize` / :func:`split_html` — the rules themselves, for callers
  that already own their layout.
* :func:`esc`, :func:`link`, :func:`bold`, :func:`heading` — HTML builders.

Nothing here reads the network, the DB or Telegram: pure layout.
"""

from __future__ import annotations

from rugmunchbot.content_style._daily_rug import (
    EDITION_TITLE as EDITION_TITLE,
    daily_rug_envelope as daily_rug_envelope,
    daily_rug_post as daily_rug_post,
)
from rugmunchbot.content_style._escape import (
    bold as bold,
    esc as esc,
    heading as heading,
    italic as italic,
    link as link,
)
from rugmunchbot.content_style._footer import (
    footer_block as footer_block,
    footer_lines as footer_lines,
)
from rugmunchbot.content_style._normalize import (
    balance_tags as balance_tags,
    dedupe_links as dedupe_links,
    normalize as normalize,
)
from rugmunchbot.content_style._prepare import prepare_for_send as prepare_for_send
from rugmunchbot.content_style._render import bullets as bullets, render_post as render_post
from rugmunchbot.content_style._rules import (
    BOT_CTA as BOT_CTA,
    BULLET as BULLET,
    EMOJI_ALLOWLIST as EMOJI_ALLOWLIST,
    HEADING_RULE as HEADING_RULE,
    MAX_HEADLINE_CHARS as MAX_HEADLINE_CHARS,
    MAX_SAME_EMOJI as MAX_SAME_EMOJI,
    PARAGRAPH_GAP as PARAGRAPH_GAP,
    SAFE_LIMIT as SAFE_LIMIT,
    SITE_CTA as SITE_CTA,
    TELEGRAM_LIMIT as TELEGRAM_LIMIT,
)
from rugmunchbot.content_style._split import split_html as split_html

render_daily_rug = daily_rug_post

__all__ = [
    "BOT_CTA",
    "BULLET",
    "EDITION_TITLE",
    "EMOJI_ALLOWLIST",
    "HEADING_RULE",
    "MAX_HEADLINE_CHARS",
    "MAX_SAME_EMOJI",
    "PARAGRAPH_GAP",
    "SAFE_LIMIT",
    "SITE_CTA",
    "TELEGRAM_LIMIT",
    "balance_tags",
    "bold",
    "bullets",
    "daily_rug_envelope",
    "daily_rug_post",
    "dedupe_links",
    "esc",
    "footer_block",
    "footer_lines",
    "heading",
    "italic",
    "link",
    "normalize",
    "prepare_for_send",
    "render_daily_rug",
    "render_post",
    "split_html",
]
