"""Lookup helpers over the public command catalog (``command_catalog``)."""

from __future__ import annotations

from rugmunchbot.command_catalog import CATALOG

#: slug → (title, emoji, items) for /more drill-downs and the help atlas.
CATALOG_GROUPS: dict[str, tuple[str, str, tuple[tuple[str, str], ...]]] = {
    slug: (title, emoji, items) for slug, title, emoji, items in CATALOG
}

#: Slug aliases so `/more scan` / `/more market` (singular) work as expected.
SLUG_ALIASES: dict[str, str] = {"all": "scans", "scan": "scans", "market": "markets"}


def resolve_group(arg: str) -> str | None:
    """Map a user argument (group slug, alias, or command name) to a slug.

    Order matters: slug → alias → command. ``market`` is both a COMMAND in
    ``scans`` and the singular ALIAS of ``markets``; the alias must win, or
    ``/more market`` silently renders the scans group (2026-10-25 audit).
    """
    wanted = (arg or "").strip().lstrip("/").lower()
    if not wanted:
        return None
    if wanted in CATALOG_GROUPS:
        return wanted
    if wanted in SLUG_ALIASES:
        return SLUG_ALIASES[wanted]
    for slug, (_title, _emoji, items) in CATALOG_GROUPS.items():
        if any(name == wanted for name, _desc in items):
            return slug
    return None


def catalog_commands() -> frozenset[str]:
    """Every command named in the catalog (public surface, admin excluded)."""
    return frozenset(name for _s, _t, _e, items in CATALOG for name, _d in items)
