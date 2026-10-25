"""Advertised command menu — derived views over ``command_registry``.

Data only (no Telegram API, no bot import) so registration wiring
(``bot/_commands.py``), the ``/help`` renderer (``handlers/_help_text.py``) and
the menu guard all read the SAME rows. The menu names/descriptions live in
``command_registry``; this module only holds the presentation ORDER of the
groups, because Telegram shows a flat list and order is not data about a
command.
"""

from __future__ import annotations

from rugmunchbot.command_registry import REGISTRY, entries

#: Hard ceiling for the advertised BotFather menu. 20 is the most a human
#: scans in one screen; the rest live under /more.
MAX_ADVERTISED = 20

#: Presentation order for the advertised groups (names/descriptions come from
#: the registry; only the sequence lives here).
MENU_GROUP_ORDER: tuple[str, ...] = (
    "Start here",
    "Scan & safety",
    "Markets",
    "Intel",
    "Account",
)


def _advertised_sections() -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    groups: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for group in MENU_GROUP_ORDER:
        rows = tuple(
            (entry.name, entry.menu_description or entry.description)
            for entry in REGISTRY
            if entry.menu_group == group
        )
        if rows:
            groups.append((group, rows))
    return tuple(groups)


def _group_admin_sections() -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    groups: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for section in dict.fromkeys(entry.section for entry in entries("group_admin")):
        rows = tuple(
            (entry.name, entry.description)
            for entry in entries("group_admin")
            if entry.section == section
        )
        groups.append((section, rows))
    return tuple(groups)


ADVERTISED_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = _advertised_sections()

#: Advertised command names, in menu order (duplicates preserved for the
#: guard to detect; do not dedupe here).
ADVERTISED_COMMANDS: tuple[str, ...] = tuple(
    command for _header, commands in ADVERTISED_SECTIONS for command, _desc in commands
)

#: The group-admin / moderation surface: registered everywhere, meaningful only
#: to a chat admin, so never advertised in the public catalogue.
GROUP_ADMIN_SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = _group_admin_sections()

#: Every documented group-admin command (flattened, one source).
GROUP_ADMIN_COMMANDS: frozenset[str] = frozenset(
    command for _h, commands in GROUP_ADMIN_SECTIONS for command, _desc in commands
)


def advertised_set() -> frozenset[str]:
    """The advertised command names as a set (menu source of truth)."""
    return frozenset(ADVERTISED_COMMANDS)


def menu_rows() -> tuple[tuple[str, str], ...]:
    """Flattened ``(command, description)`` rows in advertised order."""
    return tuple(
        (command, desc) for _h, commands in ADVERTISED_SECTIONS for command, desc in commands
    )


__all__ = [
    "ADVERTISED_COMMANDS",
    "ADVERTISED_SECTIONS",
    "GROUP_ADMIN_COMMANDS",
    "GROUP_ADMIN_SECTIONS",
    "MAX_ADVERTISED",
    "MENU_GROUP_ORDER",
    "advertised_set",
    "menu_rows",
]
