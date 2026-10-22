"""Menu-drift guard — the advertised menu must be real, small, and honest.

History. The 2026-09-20 audit found 24 registered commands missing from
``_COMMAND_SECTIONS``; the guard then failed the boot on ANY registered-but-
unadvertised command. That rule was right for its time (the menu was meant to
be the whole surface) and wrong as a product: it blocked progressive
disclosure and pushed the menu to 95 rows nobody reads.

2026-10-23 — the guard now enforces what actually hurts users:

1. **phantom** — an advertised command with no handler (a dead menu tap).
2. **ceiling** — more than ``MAX_ADVERTISED`` (20) rows in the BotFather menu.
3. **duplicates** — the same command advertised twice.

Registered-but-unadvertised is the DESIGN now: those commands live under
``/more`` (``bot/_catalog.py``) and still work. They are counted, not failed.
``ADMIN_MENU_COMMANDS`` is kept as the declared owner/admin set so the
catalog never has to advertise server-management commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

logger = get_logger("rmi_bot")

#: Owner/admin commands registered by ``handlers.admin.register_admin_commands``
#: and the owner panel. Deliberately NOT advertised anywhere public — they are
#: server-management / owner-only. The guard keeps them out of the catalog.
ADMIN_MENU_COMMANDS: frozenset[str] = frozenset(
    {
        "appeal",
        "modpardon",
        "modactions",
        "modstatus",
        "accuracy",
        "admin",
        "admin_ban",
        "admin_broadcast",
        "admin_logs",
        "admin_set_tier",
        "admin_stats",
        "admin_toggle",
        "admin_toggle_chat",
        "admin_unban",
        "admin_user",
        "backtest",
        "backup",
        "banuser",
        "budget",
        "credits",
        "deploy",
        "logs",
        "menu",
        "restart",
        "services",
        "sources",
        "users",
        "whalewatch",
    }
)


class MenuDriftError(RuntimeError):
    """Raised when the advertised menu and the registry disagree."""


def assert_menu_coverage(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Hard startup check: advertised menu is real, ≤ 20, unique.

    Raises :class:`MenuDriftError` listing every phantom command (advertised
    with no handler), every duplicate row, and the overflow count when the
    menu exceeds ``MAX_ADVERTISED``. Hidden-but-registered commands are
    intentional and never raise — they are reachable via ``/more``.

    Called from ``setup_bot_profile`` BEFORE ``set_my_commands`` so a drift
    fails the boot loudly instead of shipping a stale menu.
    """
    from rugmunchbot.bot._commands import (
        _BOT_COMMANDS,
        _MENU_COMMANDS,
        MAX_ADVERTISED,
        collect_registered_commands,
    )

    registered = collect_registered_commands(app)
    phantom = sorted(_MENU_COMMANDS - registered)
    names = [bc.command for bc in _BOT_COMMANDS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    overflow = len(_MENU_COMMANDS) - MAX_ADVERTISED

    parts: list[str] = []
    if phantom:
        parts.append(f"{len(phantom)} advertised but unregistered: {', '.join(phantom)}")
    if duplicates:
        parts.append(f"duplicate menu rows: {', '.join(duplicates)}")
    if overflow > 0:
        parts.append(f"menu has {len(_MENU_COMMANDS)} rows (ceiling {MAX_ADVERTISED})")
    if parts:
        raise MenuDriftError("MENU_DRIFT — " + "; ".join(parts))

    hidden = registered - _MENU_COMMANDS
    logger.info(
        "Menu coverage OK: %d registered, %d advertised, %d behind /more "
        "(%d owner/admin hidden)",
        len(registered),
        len(_MENU_COMMANDS),
        len(hidden - ADMIN_MENU_COMMANDS),
        len(hidden & ADMIN_MENU_COMMANDS),
    )


__all__ = [
    "ADMIN_MENU_COMMANDS",
    "MenuDriftError",
    "assert_menu_coverage",
]
