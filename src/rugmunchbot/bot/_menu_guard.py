"""Menu-drift guard — the menu is the source of truth for the user surface.

The drift this module prevents (2026-09-20 audit): 24 registered commands
were missing from ``_COMMAND_SECTIONS`` because they were registered through
paths that bypassed the menu (``register_admin_commands``, ``register_*``
helpers) and nobody noticed — the BotFather menu silently under-advertised
the bot.

Two mechanisms, both at the source:

1. :func:`assert_menu_coverage` — a HARD startup check. Unlike the
   log-only ``check_menu_coverage`` it raises ``MenuDriftError`` when the
   registry and the menu disagree, so a drift can never ship silently.
   ``_profile.setup_bot_profile`` calls it before the menu is pushed.

2. :data:`ADMIN_MENU_COMMANDS` — the owner/admin commands that are
   deliberately hidden from the public BotFather menu but MUST still be
   registered. They are declared here (one place) so the guard can tell
   "intentionally hidden" apart from "forgotten".

The guard is pure (no Telegram API, no network) so it is unit-testable
without an ``Application``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application

logger = get_logger("rmi_bot")

#: Owner/admin commands registered by ``handlers.admin.register_admin_commands``
#: and the owner panel. Deliberately NOT advertised in the public BotFather
#: menu (they are server-management / owner-only), but they ARE registered —
#: the guard treats them as known-hidden, not drift.
ADMIN_MENU_COMMANDS: frozenset[str] = frozenset(
    {
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
    """Raised when the registered command set and the menu disagree."""


def assert_menu_coverage(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Hard startup check: registry vs menu must agree.

    Raises :class:`MenuDriftError` listing every unadvertised command (a
    registered command missing from the menu) and every phantom command (a
    menu entry with no handler). ``ADMIN_MENU_COMMANDS`` are exempt from the
    unadvertised check — they are intentionally hidden.

    Called from ``setup_bot_profile`` BEFORE ``set_my_commands`` so a drift
    fails the boot loudly instead of shipping a stale menu.
    """
    from rugmunchbot.bot._commands import (
        _MENU_COMMANDS,
        collect_registered_commands,
    )

    registered = collect_registered_commands(app)
    unadvertised = sorted(registered - _MENU_COMMANDS - ADMIN_MENU_COMMANDS)
    phantom = sorted(_MENU_COMMANDS - registered)
    if unadvertised or phantom:
        parts: list[str] = []
        if unadvertised:
            parts.append(
                f"{len(unadvertised)} registered but unadvertised: {', '.join(unadvertised)}"
            )
        if phantom:
            parts.append(f"{len(phantom)} advertised but unregistered: {', '.join(phantom)}")
        raise MenuDriftError("MENU_DRIFT — " + "; ".join(parts))
    logger.info(
        "Menu coverage OK: %d registered, %d advertised, %d admin-hidden",
        len(registered),
        len(_MENU_COMMANDS),
        len(ADMIN_MENU_COMMANDS),
    )


__all__ = [
    "ADMIN_MENU_COMMANDS",
    "MenuDriftError",
    "assert_menu_coverage",
]
