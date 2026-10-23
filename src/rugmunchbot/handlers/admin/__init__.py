#!/usr/bin/env python3
"""
RugMunchBot -- @rmi_admin_bot server-management command handlers.

Owner-only commands that turn the bot into a server management tool. Each
command is owner-gated (``OWNER_IDS`` via ``is_owner``), executes through
``_exec`` (subprocess, 10s timeout, ``shell=False``), and replies as HTML.

Layout
------
  _constants.py  -- service/repo allowlists, timeouts, env-overridable paths
  _exec.py       -- subprocess seam (``_run`` / ``_run_async``)
  _reply.py      -- owner gate (``_deny``) + HTML reply (``_reply``)
  _status.py     -- /status, /services
  _restart.py    -- /restart, /logs
  _deploy.py     -- /deploy, /backup
  _info.py       -- /alerts, /budget, /sources
  _help.py       -- /help
  _register.py   -- ``register_admin_commands(app)``

Callers keep importing ``handlers.admin`` exactly as before.
"""

from __future__ import annotations

# Imported first so the handler modules can resolve ``_exec`` / ``_reply``
# while this package is still initialising.
from rugmunchbot.handlers.admin import _constants, _exec, _reply  # noqa: F401
from rugmunchbot.handlers.admin._accuracy import accuracy_command
from rugmunchbot.handlers.admin._backtest import backtest_command
from rugmunchbot.handlers.admin._constants import (
    _BACKUP_SCRIPT,
    _CMD_TIMEOUT,
    _DEPLOY_TARGETS,
    _LOG_LINES,
    _REPO_ROOT,
    _RMI_SERVICES,
)
from rugmunchbot.handlers.admin._deploy import cmd_backup, cmd_deploy
from rugmunchbot.handlers.admin._exec import _run, _run_async
from rugmunchbot.handlers.admin._help import cmd_help
from rugmunchbot.handlers.admin._info import cmd_alerts, cmd_budget, cmd_sources
from rugmunchbot.handlers.admin._register import register_admin_commands
from rugmunchbot.handlers.admin._reply import _deny
from rugmunchbot.handlers.admin._restart import cmd_logs, cmd_restart
from rugmunchbot.handlers.admin._status import cmd_services, cmd_status
from rugmunchbot.states import is_owner

__all__ = [
    "_BACKUP_SCRIPT",
    "_CMD_TIMEOUT",
    "_DEPLOY_TARGETS",
    "_LOG_LINES",
    "_REPO_ROOT",
    "_RMI_SERVICES",
    "_deny",
    "_run",
    "_run_async",
    "accuracy_command",
    "backtest_command",
    "cmd_alerts",
    "cmd_backup",
    "cmd_budget",
    "cmd_deploy",
    "cmd_help",
    "cmd_logs",
    "cmd_restart",
    "cmd_services",
    "cmd_sources",
    "cmd_status",
    "is_owner",
    "register_admin_commands",
]
