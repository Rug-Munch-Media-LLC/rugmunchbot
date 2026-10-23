"""Bot -> website deep links: one place where a command meets its page.

Website parity (2026-10-24). The site is the full UI; the bot is the fast
lane. A command that mirrors a website surface never re-implements it: it
renders a compact summary and hands off with a single "Open on rugmunch.io"
button, so the two surfaces cannot drift apart.

Paths mirror rmi-frontend ``ROUTE_PATHS`` -- ``/scam-school``, not the
legacy ``/scamschool`` alias (a deep link must never take a redirect).

Only the keyboard type is imported: this module is importable before the
Telegram application exists (no circular import).
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import WEBSITE_URL

#: Surface key -> canonical path on rugmunch.io (rmi-frontend ROUTE_PATHS).
SITE_PATHS: dict[str, str] = {
    "news": "/news",
    "docs": "/docs",
    "mcpdocs": "/mcp-docs",
    "scamschool": "/scam-school",
    "portfolio": "/portfolio",
    "dashboard": "/dashboard",
    "pricing": "/pricing",
}

#: Label on every hand-off button (identical on purpose -- one affordance).
SITE_LABEL = "\U0001f310 Open on rugmunch.io"


def site_url(surface: str) -> str:
    """Absolute URL for *surface*. An unknown key raises, never a silent home page."""
    try:
        path = SITE_PATHS[surface]
    except KeyError as exc:
        raise KeyError(f"unknown site surface: {surface!r}") from exc
    return f"{WEBSITE_URL}{path}"


def site_row(surface: str, label: str = SITE_LABEL) -> list[InlineKeyboardButton]:
    """One URL-button row that opens *surface* on rugmunch.io."""
    return [InlineKeyboardButton(label, url=site_url(surface))]


def with_site_link(
    surface: str, markup: InlineKeyboardMarkup | None = None
) -> InlineKeyboardMarkup:
    """Return a copy of *markup* with the hand-off row appended (input untouched)."""
    rows = [list(row) for row in (markup.inline_keyboard if markup else [])]
    rows.append(site_row(surface))
    return InlineKeyboardMarkup(rows)
