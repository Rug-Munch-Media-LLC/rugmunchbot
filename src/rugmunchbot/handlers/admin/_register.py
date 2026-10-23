"""Wire the owner-only admin commands onto a Telegram Application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.handlers.admin._accuracy import accuracy_command
from rugmunchbot.handlers.admin._backtest import backtest_command
from rugmunchbot.handlers.admin._deploy import cmd_backup, cmd_deploy
from rugmunchbot.handlers.admin._help import cmd_help
from rugmunchbot.handlers.admin._info import cmd_alerts, cmd_budget, cmd_sources
from rugmunchbot.handlers.admin._restart import cmd_logs, cmd_restart
from rugmunchbot.handlers.admin._status import cmd_services, cmd_status

if TYPE_CHECKING:
    from telegram.ext import Application

#: (command name, handler) in registration order.
_ADMIN_COMMANDS = (
    ("status", cmd_status),
    ("services", cmd_services),
    ("restart", cmd_restart),
    ("logs", cmd_logs),
    ("deploy", cmd_deploy),
    ("backup", cmd_backup),
    ("alerts", cmd_alerts),
    ("budget", cmd_budget),
    ("sources", cmd_sources),
    ("backtest", backtest_command),
    ("accuracy", accuracy_command),
    ("help", cmd_help),
)


def register_admin_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register every @rmi_admin_bot command on *app*.

    Uses the shared ``_reg`` helper so these commands get the same per-command
    activity tracking + flood gate as every other command, and so the
    menu-drift guard sees them. They stay OUT of ``_COMMAND_SECTIONS``
    (owner-only, hidden from BotFather) and are declared in
    ``bot._menu_guard.ADMIN_MENU_COMMANDS``.
    """
    from rugmunchbot.bot._commands import _reg

    for name, handler in _ADMIN_COMMANDS:
        _reg(app, name, handler)
