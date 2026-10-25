"""One registry for every bot command — name, section, description, audience.

Before 2026-10-25 the surface lived in four places (``command_catalog.CATALOG``,
``command_menu.ADVERTISED_SECTIONS``, ``command_menu.GROUP_ADMIN_SECTIONS``,
``bot._menu_guard.ADMIN_MENU_COMMANDS``) and the audit tests reconciled them by
hand — which is how a menu row and a handler drift apart. Now the data lives in
``_command_registry_data`` and every view derives from here:

* BotFather menu + ``/help``  -> ``command_menu.ADVERTISED_SECTIONS``
* ``/more`` catalogue         -> ``command_catalog.CATALOG``
* owner/admin set             -> ``bot._menu_guard.ADMIN_MENU_COMMANDS``
* surface audit tests         -> ``tests/test_command_registry.py``

Nothing here imports telegram, handlers or bot: the registry is data, importable
before the application exists (locked by an import contract + AST test).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rugmunchbot._command_registry_data import ENTRIES

Audience = Literal["public", "group_admin", "owner"]

AUDIENCES: tuple[Audience, ...] = ("public", "group_admin", "owner")


@dataclass(frozen=True, slots=True)
class CommandEntry:
    """One command: who may use it, where it is documented, who handles it."""

    name: str
    section: str
    description: str
    audience: Audience = "public"
    handler: str | None = None
    menu_group: str | None = None
    menu_description: str | None = None


def _build() -> tuple[CommandEntry, ...]:
    return tuple(CommandEntry(**row) for row in ENTRIES)


#: The registry. Order is stable: public catalogue order, then group-admin, then owner.
REGISTRY: tuple[CommandEntry, ...] = _build()
_BY_NAME: dict[str, CommandEntry] = {entry.name: entry for entry in REGISTRY}


def get(name: str) -> CommandEntry | None:
    """The entry for *name*, or ``None`` when the command is not registered here."""
    return _BY_NAME.get(name.lstrip("/").lower())


def entries(audience: Audience | None = None) -> tuple[CommandEntry, ...]:
    """Registry entries, optionally filtered to one audience."""
    if audience is None:
        return REGISTRY
    return tuple(entry for entry in REGISTRY if entry.audience == audience)


def names(audience: Audience | None = None) -> frozenset[str]:
    """Command names, optionally filtered to one audience."""
    return frozenset(entry.name for entry in entries(audience))


def advertised() -> tuple[CommandEntry, ...]:
    """Entries the BotFather menu advertises, in registry order."""
    return tuple(entry for entry in REGISTRY if entry.menu_group)


def sections(audience: Audience) -> tuple[str, ...]:
    """Section titles for *audience* in first-appearance order."""
    seen: dict[str, None] = {}
    for entry in entries(audience):
        seen.setdefault(entry.section, None)
    return tuple(seen)


__all__ = [
    "AUDIENCES",
    "REGISTRY",
    "Audience",
    "CommandEntry",
    "advertised",
    "entries",
    "get",
    "names",
    "sections",
]
