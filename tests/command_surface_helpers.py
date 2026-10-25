"""Shared registry/parity helpers for the command-surface tests (2026-10-25)."""

from __future__ import annotations

import re
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from telegram.ext import Application, CommandHandler

from rugmunchbot.command_catalog import CATALOG
from rugmunchbot.command_menu import advertised_set
from rugmunchbot.handlers._help_text import HELP_TEXT, MENU_TEXT


@lru_cache(maxsize=1)
def build_app() -> Application:
    """The real wiring, built once per test session (no polling, no network)."""
    from rugmunchbot.bot._commands import register_surface_commands
    from rugmunchbot.bot._entry import _register_all_handlers

    app = Application.builder().token("123456:TEST-TOKEN").build()
    _register_all_handlers(app)
    register_surface_commands(app)
    return app


def registered() -> set[str]:
    from rugmunchbot.bot._commands import collect_registered_commands

    return collect_registered_commands(build_app())


def catalog() -> set[str]:
    return {name for _s, _t, _e, items in CATALOG for name, _d in items}


def help_mentions() -> set[str]:
    return set(re.findall(r"(?<![\w./@<])/([a-z_]{2,30})\b", HELP_TEXT + MENU_TEXT))


def documented() -> set[str]:
    from rugmunchbot.bot._menu_guard import ADMIN_MENU_COMMANDS
    from rugmunchbot.command_menu import GROUP_ADMIN_COMMANDS

    return (
        catalog() | advertised_set() | help_mentions() | GROUP_ADMIN_COMMANDS | ADMIN_MENU_COMMANDS
    )


def callbacks() -> dict[str, Any]:
    found: dict[str, Any] = {}
    for group in build_app().handlers.values():
        for handler in group:
            if isinstance(handler, CommandHandler):
                for name in handler.commands:
                    found.setdefault(name, handler.callback)
    return found


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kw: Any) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text: str = "", user_id: int = 999) -> None:
        self.message = FakeMessage(text)
        self.effective_message = self.message
        self.effective_user = SimpleNamespace(id=user_id, username="tester")
        self.effective_chat = SimpleNamespace(id=-100123)


async def noop(update: Any, context: Any) -> None:  # pragma: no cover - stub
    return None
