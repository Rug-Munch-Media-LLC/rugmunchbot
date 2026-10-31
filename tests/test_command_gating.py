"""Command gating — owner, premium, aliases and unknown commands.

Every privileged command must refuse the caller BEFORE it does work, and a typo
must get an answer instead of silence (before 2026-10-25 an unknown command was
swallowed with no reply at all).
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from _di import _setattr
from command_surface_helpers import FakeUpdate, callbacks, noop

from rugmunchbot.bot._menu_guard import ADMIN_MENU_COMMANDS
from rugmunchbot.handlers._help_text import MENU_TEXT

#: Tokens that prove a command checks permission before acting — one place, so
#: the ratchet is explicit and reviewable.
GATE_TOKENS = (
    "is_owner",
    "OWNER_IDS",
    "_deny",
    "_allowed",
    "require_chat_admin",
    "require_tier",
    "get_user_tier",
    "paywall",
    "PREMIUM",
    "owner_only",
    "is_admin",
    "ADMIN_IDS",
    "_gate(",
)


def test_every_owner_command_checks_a_gate():
    index = callbacks()
    ungated = []
    for name in sorted(ADMIN_MENU_COMMANDS):
        callback = index[name]
        try:
            source = inspect.getsource(inspect.unwrap(callback))
        except (OSError, TypeError):  # pragma: no cover - builtins only
            continue
        if not any(token in source for token in GATE_TOKENS):
            ungated.append(name)
    assert not ungated, f"owner/admin commands with no gate: {ungated}"


def test_owner_command_refuses_a_non_owner_before_working():
    from rugmunchbot.handlers.admin import cmd_backup

    update = FakeUpdate("/backup")
    asyncio.run(cmd_backup(update, SimpleNamespace(args=[], bot=None)))
    assert update.message.replies
    assert "Owner only" in update.message.replies[0]


def test_premium_command_refuses_free_tier(monkeypatch):
    from rugmunchbot.handlers import whalewatch

    _setattr(whalewatch.db, "get_user_tier", lambda _uid: "free")
    update = FakeUpdate("/whalewatch 0xabc")
    asyncio.run(whalewatch.cmd_whalewatch(update, SimpleNamespace(args=["0xabc"], bot=None)))
    assert any("Scout tier" in reply for reply in update.message.replies)
    assert not any("Profiling" in reply for reply in update.message.replies)


def test_premium_command_explains_usage_without_args():
    from rugmunchbot.handlers import whalewatch

    update = FakeUpdate("/whalewatch")
    asyncio.run(whalewatch.cmd_whalewatch(update, SimpleNamespace(args=[], bot=None)))
    assert any("Usage" in reply for reply in update.message.replies)


def test_menu_is_an_alias_of_more():
    from rugmunchbot.handlers._core_handlers import cmd_menu
    from rugmunchbot.handlers._more import cmd_more

    menu_update, more_update = FakeUpdate("/menu"), FakeUpdate("/more")
    asyncio.run(cmd_menu(menu_update, SimpleNamespace(args=[], bot=None)))
    asyncio.run(cmd_more(more_update, SimpleNamespace(args=[], bot=None)))
    assert menu_update.message.replies == more_update.message.replies == [MENU_TEXT]


def test_more_slug_aliases_resolve_before_command_names():
    from rugmunchbot.command_lookup import resolve_group

    assert resolve_group("/scan") == "scans"
    assert resolve_group("market") == "markets", "alias must beat the /market command"
    assert resolve_group("all") == "scans"
    assert resolve_group("desk") == "desk"
    assert resolve_group("nope-nope") is None


def test_unknown_command_gets_a_hint():
    from telegram.ext import CommandHandler

    from rugmunchbot.handlers._unknown import cmd_unknown

    app = SimpleNamespace(handlers={0: [CommandHandler("scan", noop)]})
    update = FakeUpdate("/scna")
    asyncio.run(cmd_unknown(update, SimpleNamespace(application=app)))
    assert update.message.replies
    assert "not a command" in update.message.replies[0]
    assert "/help" in update.message.replies[0]


def test_known_command_is_never_answered_by_the_fallback():
    from telegram.ext import CommandHandler

    from rugmunchbot.handlers._unknown import cmd_unknown

    app = SimpleNamespace(handlers={0: [CommandHandler("scan", noop)]})
    update = FakeUpdate("/scan 0xabc")
    asyncio.run(cmd_unknown(update, SimpleNamespace(application=app)))
    assert update.message.replies == []
