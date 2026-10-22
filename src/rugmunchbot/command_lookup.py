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
    """Map a user argument (group slug OR command name) to a catalog slug."""
    wanted = (arg or "").strip().lstrip("/").lower()
    if not wanted:
        return None
    if wanted in CATALOG_GROUPS:
        return wanted
    for slug, (_title, _emoji, items) in CATALOG_GROUPS.items():
        if any(name == wanted for name, _desc in items):
            return slug
    return SLUG_ALIASES.get(wanted)


def catalog_commands() -> frozenset[str]:
    """Every command named in the catalog (public surface, admin excluded)."""
    return frozenset(name for _s, _t, _e, items in CATALOG for name, _d in items)
