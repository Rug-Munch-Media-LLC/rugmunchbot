"""Full public command catalog for /more — derived from ``command_registry``.

Every entry is a REGISTERED command that works today. The BotFather menu
advertises only the top ``command_menu.MAX_ADVERTISED``; this catalog is the
complete public surface rendered by ``/more`` and its drill-downs. Descriptions
live in ``command_registry`` ONCE, so ``/help``, ``/more`` and the menu guard
cannot describe the same command differently. Presentation (section order and
icons) lives here — it is not data about a command.
"""

from __future__ import annotations

from rugmunchbot.command_registry import entries

#: ``(key, title, icon)`` in /more display order. Titles must match the
#: ``section`` recorded on each public registry entry.
_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("desk", "The Desk", "\U0001f4f0"),
    ("channels", "Channels & Community", "\U0001f4e2"),
    ("scans", "Scan & Safety", "\U0001f6e1\ufe0f"),
    ("forensics", "Security Forensics", "\U0001f52c"),
    ("markets", "Markets & Discovery", "\U0001f4c8"),
    ("power", "Power Tools", "\U0001f9f0"),
    ("intel", "Pro Intel", "\U0001f3af"),
    ("osint", "OSINT & Identity", "\U0001f575\ufe0f"),
    ("alerts", "Watch & Alerts", "\U0001f514"),
    ("account", "Account & Settings", "\U0001f464"),
)


def _catalog() -> tuple[tuple[str, str, str, tuple[tuple[str, str], ...]], ...]:
    public = entries("public")
    return tuple(
        (
            key,
            title,
            icon,
            tuple((entry.name, entry.description) for entry in public if entry.section == title),
        )
        for key, title, icon in _SECTIONS
    )


CATALOG: tuple[tuple[str, str, str, tuple[tuple[str, str], ...]], ...] = _catalog()

__all__ = ["CATALOG"]
