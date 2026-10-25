"""Command-surface parity — registered ⇄ documented, in both directions.

The /whales incident was one shape of a general bug: the bot claimed a command
no handler served. ``test_command_surface.py`` checks catalog → registry; this
module closes the reverse direction (registered but documented nowhere is
either dead code or a missing /help line) and keeps the owner surface honest:
no owner-only command may appear in the public catalogue.
"""

from __future__ import annotations

from command_surface_helpers import catalog, documented, registered

from rugmunchbot.bot._menu_guard import ADMIN_MENU_COMMANDS
from rugmunchbot.command_menu import GROUP_ADMIN_COMMANDS


def test_every_registered_command_is_documented():
    undocumented = sorted(registered() - documented())
    assert not undocumented, f"registered but documented nowhere: {undocumented}"


def test_every_documented_command_is_registered():
    missing = sorted(documented() - registered())
    assert not missing, f"documented but not registered: {missing}"


def test_group_admin_surface_is_registered():
    assert registered() >= GROUP_ADMIN_COMMANDS


def test_owner_surface_is_registered():
    assert registered() >= ADMIN_MENU_COMMANDS


def test_public_catalog_never_advertises_an_owner_command():
    leaked = sorted(catalog() & ADMIN_MENU_COMMANDS)
    assert not leaked, f"public catalog advertises owner-only commands: {leaked}"


def test_admin_set_excludes_public_and_premium_commands():
    """/menu (alias of /more), /whalewatch (premium tier) and /appeal (member)."""
    assert not {"menu", "whalewatch", "appeal"} & ADMIN_MENU_COMMANDS
