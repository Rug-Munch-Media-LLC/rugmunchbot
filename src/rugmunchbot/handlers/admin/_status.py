"""Read-only health commands: /status and /services."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from rugmunchbot.handlers.admin import _exec, _reply
from rugmunchbot.handlers.admin._constants import _RMI_SERVICES

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show server health: CPU, RAM, disk, Docker containers, systemd, uptime."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    lines: list[str] = ["\U0001f4ca <b>Server Status</b>", "\u2500" * 20]

    rc, out = _exec._run(["uptime"])
    if rc == 0 and out:
        lines.append(f"\U0001f504 <b>Uptime:</b> <code>{html.escape(out)}</code>")

    rc, out = _exec._run(["top", "-bn1"])
    if rc == 0 and out:
        for line in out.splitlines():
            if line.startswith(("%Cpu", "MiB Mem", "KiB Mem")):
                lines.append(f"\U0001f4be <code>{html.escape(line.strip())}</code>")

    rc, out = _exec._run(["df", "-h", "/"])
    if rc == 0 and out:
        for line in out.splitlines()[1:2]:
            parts = line.split()
            if len(parts) >= 5:
                lines.append(
                    f"\U0001f4bd <b>Disk /:</b> <code>{parts[1]}</code> used ({parts[4]} full)"
                )

    rc, out = _exec._run(["docker", "ps", "--format", "{{.Names}}: {{.Status}}"])
    if rc == 0 and out:
        lines.append("\U0001f433 <b>Docker containers:</b>")
        for c in out.splitlines()[:12]:
            lines.append(f"  \u2022 <code>{html.escape(c)}</code>")
    else:
        lines.append("\U0001f433 <b>Docker:</b> <i>unavailable</i>")

    rc, out = _exec._run(["systemctl", "is-active", "rugmunchbot", "rmi-backend"])
    if rc == 0 and out:
        lines.append(f"\U0001f3e2 <b>systemd:</b> <code>{html.escape(out)}</code>")

    await _reply._reply(update, "\n".join(lines))


async def cmd_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List all RMI services with status (running/stopped)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    lines: list[str] = ["\U0001f3e2 <b>RMI Services</b>", "\u2500" * 20]

    for svc in _RMI_SERVICES:
        # Prefer Docker; fall back to systemd.
        rc, out = _exec._run(["docker", "inspect", "-f", "{{.State.Status}}", svc])
        if rc == 0 and out:
            status = out.strip()
            emoji = "\u2705" if status == "running" else "\u26d4"
            lines.append(f"{emoji} <code>{svc}</code> \u2014 {status}")
            continue
        rc2, out2 = _exec._run(["systemctl", "is-active", svc])
        if rc2 == 0:
            status = out2.strip()
            emoji = "\u2705" if status == "active" else "\u26d4"
            lines.append(f"{emoji} <code>{svc}</code> \u2014 {status}")
        else:
            lines.append(f"\u2753 <code>{svc}</code> \u2014 unknown")

    await _reply._reply(update, "\n".join(lines))
