"""/more — the full command catalog, grouped, with per-group drill-down.

`/more` (no args)   → index: every group, its size, three examples, slug.
`/more <slug>`      → every command in that group with its one-line purpose.
`/more <command>`   → the group that contains that command.

Everything is rendered from ``bot/_catalog.py`` — the same object the menu
guard validates — so this surface cannot advertise a command that does not
exist (``check_menu_coverage`` re-checks every ``/cmd`` mention at startup).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.command_lookup import CATALOG_GROUPS
from rugmunchbot.handlers._help_text import MENU_TEXT

_ALIASES: dict[str, str] = {"all": "scan", "scan": "scans", "market": "markets"}


def render_group(slug: str) -> str | None:
    """Render one catalog group (HTML), or ``None`` when the slug is unknown."""
    key = _ALIASES.get(slug.lower(), slug.lower())
    group = CATALOG_GROUPS.get(key)
    if group is None:
        return None
    title, emoji, items = group
    lines = [
        f"{emoji} <b>{title}</b> \u2014 {len(items)} commands",
        "\u2501" * 26,
    ]
    for name, desc in items:
        lines.append(f"/{name} \u2014 {desc}")
    others = " \u00b7 ".join(f"/more {other}" for other in CATALOG_GROUPS if other != key)
    lines.append(f"\nIndex: /more \u00b7 Other groups: {others}")
    return "\n".join(lines)


def _resolve(arg: str) -> str | None:
    """Map a user argument (slug or command name) to a catalog group slug."""
    wanted = arg.strip().lstrip("/").lower()
    if not wanted:
        return None
    for slug, (_title, _emoji, items) in CATALOG_GROUPS.items():
        if wanted == slug or any(name == wanted for name, _desc in items):
            return slug
    return _ALIASES.get(wanted)


def render_more(arg: str = "") -> str:
    """Render the /more surface for *arg* (index when empty, else a group)."""
    slug = _resolve(arg)
    if not arg.strip():
        return MENU_TEXT
    if slug is None:
        return f"No group or command called <b>{arg.strip()[:40]}</b>.\n\n{MENU_TEXT}"
    return render_group(slug) or MENU_TEXT


async def cmd_more(update: Any, ctx: Any) -> None:
    """/more [group|command] — the complete catalog."""
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    args = getattr(ctx, "args", None) or []
    await message.reply_text(
        render_more(" ".join(args)),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


__all__ = ["cmd_more", "render_group", "render_more"]
