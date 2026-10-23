"""The /help command listing every owner-only admin command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot.handlers.admin import _reply
from rugmunchbot.handlers.admin._constants import _DEPLOY_TARGETS, _RMI_SERVICES

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List all admin commands (owner only)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    lines = [
        "\U0001f6e1\ufe0f <b>@rmi_admin_bot \u2014 Server Management</b>",
        "\u2500" * 20,
        "/status \u2014 server health (CPU, RAM, disk, Docker, systemd, uptime)",
        "/services \u2014 list all RMI services with status",
        "/restart &lt;service&gt; \u2014 restart a Docker container or systemd service",
        "/logs &lt;service&gt; \u2014 last 20 lines of a service's logs",
        "/deploy &lt;repo&gt; \u2014 git pull + restart the service for a repo",
        "/backup \u2014 manual backup of Postgres + ClickHouse",
        "/alerts \u2014 recent system alerts from the Redis queue",
        "/budget \u2014 X-intel budget status (owned by rugbrain)",
        "/sources \u2014 active content sources (RSS + Telegram + X)",
        "/help \u2014 this list",
        "",
        f"Services: {', '.join(_RMI_SERVICES)}",
        f"Repos: {', '.join(_DEPLOY_TARGETS)}",
    ]
    await _reply._reply(update, "\n".join(lines))
