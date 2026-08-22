"""RugMunchBot application wiring (package).

Modularized from the former single-module ``bot.py`` (2026-08-21). This
``__init__`` is a pure re-export shim: every public name and signature is
unchanged. Launch paths keep working:

  * ``python -m rugmunchbot.bot``      -> ``bot/__main__.py`` (Docker CMD)
  * ``secure_launcher rugmunchbot``    -> imports this package, calls ``main()``
  * ``from rugmunchbot.bot import main`` -> unchanged

Layout:
  * ``_commands.py`` — /help sections + BotFather command sync
  * ``_jobs.py``     — core APScheduler jobs (metrics heartbeat, channels)
  * ``_gates.py``    — maintenance gate + ban gate
  * ``_profile.py``  — bot profile setup + daily scam-school tip
  * ``_shutdown.py`` — SIGTERM graceful shutdown + headless teardown
  * ``_entry.py``    — main() startup orchestration
"""

from __future__ import annotations

from rugmunchbot.bot._commands import (
    _BOT_COMMANDS as _BOT_COMMANDS,
    _COMMAND_SECTIONS as _COMMAND_SECTIONS,
    _reg as _reg,
)
from rugmunchbot.bot._entry import main as main
from rugmunchbot.bot._gates import (
    _send_ban_notice as _send_ban_notice,
    install_ban_gate as install_ban_gate,
    install_maintenance_gate as install_maintenance_gate,
)
from rugmunchbot.bot._jobs import (
    _metrics_job as _metrics_job,
    register_core_jobs as register_core_jobs,
)
from rugmunchbot.bot._profile import (
    daily_scam_tip as daily_scam_tip,
    setup_bot_profile as setup_bot_profile,
)
from rugmunchbot.bot._shutdown import (
    _graceful_shutdown as _graceful_shutdown,
    _shutdown_tasks as _shutdown_tasks,
    close_headless_on_shutdown as close_headless_on_shutdown,
    install_sigterm_shutdown as install_sigterm_shutdown,
)
from rugmunchbot.config import (
    ALERT_CHECK_INTERVAL as ALERT_CHECK_INTERVAL,
)
from rugmunchbot.handlers.alerts import (
    MONITOR_CHECK_INTERVAL as MONITOR_CHECK_INTERVAL,
)

__all__ = [
    "close_headless_on_shutdown",
    "daily_scam_tip",
    "install_ban_gate",
    "install_maintenance_gate",
    "install_sigterm_shutdown",
    "main",
    "register_core_jobs",
    "setup_bot_profile",
]
