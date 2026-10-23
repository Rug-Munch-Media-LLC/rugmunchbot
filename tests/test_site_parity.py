"""Bot <-> website parity contracts (2026-10-24).

Five website-only abilities are now reachable from Telegram: news, docs, MCP
docs, scam school, portfolio and the dashboard. The bot must NOT re-implement
the web UI -- it renders a compact summary and hands off with an
"Open on rugmunch.io" button. These tests lock the three things that make the
hand-off safe:

1. every surface maps to the canonical rmi-frontend path (``/scam-school``,
   not the ``/scamschool`` redirect) and an unknown key raises;
2. ``with_site_link`` appends the URL row, never mutates the input markup,
   and preserves the callback rows it was given;
3. the commands are registered on the real wiring and render the button with
   a fake message -- no network, no Telegram.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import WEBSITE_URL
from rugmunchbot.handlers._site_links import (
    SITE_LABEL,
    SITE_PATHS,
    site_row,
    site_url,
    with_site_link,
)

EXPECTED_PATHS = {
    "news": "/news",
    "docs": "/docs",
    "mcpdocs": "/mcp-docs",
    "scamschool": "/scam-school",
    "portfolio": "/portfolio",
    "dashboard": "/dashboard",
    "pricing": "/pricing",
}


def test_paths_match_website_routes() -> None:
    assert SITE_PATHS == EXPECTED_PATHS
    for surface, path in EXPECTED_PATHS.items():
        assert site_url(surface) == f"{WEBSITE_URL}{path}"
        assert site_url(surface).startswith("https://")


def test_unknown_surface_raises() -> None:
    with pytest.raises(KeyError):
        site_url("scamschool-legacy")


def test_site_row_is_a_url_button() -> None:
    (button,) = site_row("news")
    assert isinstance(button, InlineKeyboardButton)
    assert button.text == SITE_LABEL
    assert button.url == f"{WEBSITE_URL}/news"


def test_with_site_link_preserves_rows_and_input() -> None:
    original = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Refresh", callback_data="news_refresh")]]
    )
    merged = with_site_link("news", original)

    assert len(original.inline_keyboard) == 1  # input untouched (no mutation)
    assert len(merged.inline_keyboard) == 2
    assert merged.inline_keyboard[0][0].callback_data == "news_refresh"
    assert merged.inline_keyboard[-1][0].url == f"{WEBSITE_URL}/news"


def test_with_site_link_from_empty() -> None:
    markup = with_site_link("dashboard", None)
    rows = [list(row) for row in markup.inline_keyboard]
    assert rows == [[InlineKeyboardButton(SITE_LABEL, url=f"{WEBSITE_URL}/dashboard")]]


def test_existing_keyboards_gain_the_hand_off_row() -> None:
    """The advertised keyboards keep their rows and gain exactly one URL row."""
    from rugmunchbot.keyboards import pricing_kb, scamschool_kb

    for surface, markup in (("scamschool", scamschool_kb()), ("pricing", pricing_kb())):
        before = len(markup.inline_keyboard)
        merged = with_site_link(surface, markup)
        assert len(merged.inline_keyboard) == before + 1
        assert merged.inline_keyboard[-1][0].url == site_url(surface)
        assert merged.inline_keyboard[0][0].callback_data == markup.inline_keyboard[0][0].callback_data


@pytest.fixture(scope="module")
def registered() -> set[str]:
    from telegram.ext import Application

    from rugmunchbot.bot._commands import (
        collect_registered_commands,
        register_surface_commands,
    )
    from rugmunchbot.bot._entry import _register_all_handlers

    app = Application.builder().token("123456:TEST-TOKEN").build()
    _register_all_handlers(app)
    register_surface_commands(app)
    return collect_registered_commands(app)


def test_site_commands_registered(registered: set[str]) -> None:
    missing = {"docs", "mcpdocs", "dashboard"} - registered
    assert not missing, f"site commands not wired: {sorted(missing)}"


class FakeMessage:
    def __init__(self) -> None:
        self.text: str | None = None
        self.markup: Any = None
        self.kwargs: dict[str, Any] = {}

    async def reply_text(self, text: str, **kwargs: Any) -> "FakeMessage":
        self.text, self.kwargs = text, kwargs
        self.markup = kwargs.get("reply_markup")
        return self


@pytest.mark.parametrize(
    ("module", "handler_name", "surface"),
    [
        ("rugmunchbot.handlers._site", "cmd_docs", "docs"),
        ("rugmunchbot.handlers._site", "cmd_mcpdocs", "mcpdocs"),
        ("rugmunchbot.handlers._site", "cmd_dashboard", "dashboard"),
    ],
)
@pytest.mark.asyncio
async def test_handler_renders_hand_off_button(
    module: str, handler_name: str, surface: str
) -> None:
    import importlib

    handler = getattr(importlib.import_module(module), handler_name)
    message = FakeMessage()
    update = SimpleNamespace(effective_message=message)

    await handler(update, SimpleNamespace(args=[]))

    assert message.text, "handler rendered no text"
    assert message.kwargs.get("parse_mode") == "HTML"
    urls = [b.url for row in message.markup.inline_keyboard for b in row if b.url]
    assert f"{WEBSITE_URL}{EXPECTED_PATHS[surface]}" in urls
    # the hand-off label is identical everywhere (one affordance)
    labels = [b.text for row in message.markup.inline_keyboard for b in row if b.url]
    assert SITE_LABEL in labels


@pytest.mark.asyncio
async def test_docs_cross_links_mcp() -> None:
    from rugmunchbot.handlers._site import cmd_docs

    message = FakeMessage()
    await cmd_docs(SimpleNamespace(effective_message=message), SimpleNamespace(args=[]))
    urls = {b.url for row in message.markup.inline_keyboard for b in row if b.url}
    assert f"{WEBSITE_URL}/docs" in urls
    assert f"{WEBSITE_URL}/mcp-docs" in urls


@pytest.mark.asyncio
async def test_handler_without_message_is_safe() -> None:
    from rugmunchbot.handlers._site import cmd_dashboard

    await cmd_dashboard(SimpleNamespace(effective_message=None), SimpleNamespace(args=[]))
