"""Mutating service commands: /restart and /logs."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from rugmunchbot.handlers.admin import _exec, _reply
from rugmunchbot.handlers.admin._constants import _LOG_LINES, _RMI_SERVICES

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


def _unknown_service(svc: str) -> str:
    """Build the rejection message for a service name outside the allowlist."""
    return (
        f"\u274c Unknown service <code>{html.escape(svc)}</code>.\n"
        f"Allowed: {', '.join(_RMI_SERVICES)}"
    )


async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart a Docker container or systemd service (owner only)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    args = (ctx.args or []) if ctx else []
    if not args:
        await _reply._reply(
            update,
            "\U0001f504 <b>Usage:</b> <code>/restart &lt;service&gt;</code>\n"
            f"Services: {', '.join(_RMI_SERVICES)}",
        )
        return

    svc = args[0].strip().lower()
    if svc not in _RMI_SERVICES:
        await _reply._reply(update, _unknown_service(svc))
        return

    # Try Docker first, then systemd.
    rc, out = _exec._run(["docker", "inspect", "-f", "{{.State.Status}}", svc])
    if rc == 0 and out:
        rc2, out2 = _exec._run(["docker", "restart", svc])
        if rc2 == 0:
            await _reply._reply(update, f"\U0001f504 <b>{svc}</b> restarted (Docker).")
        else:
            await _reply._reply(
                update,
                f"\u274c Failed to restart <code>{svc}</code> (Docker): "
                f"<code>{html.escape(out2[:200])}</code>",
            )
        return

    rc3, out3 = _exec._run(["systemctl", "restart", svc])
    if rc3 == 0:
        await _reply._reply(update, f"\U0001f504 <b>{svc}</b> restarted (systemd).")
    else:
        await _reply._reply(
            update,
            f"\u274c Failed to restart <code>{svc}</code> (systemd): "
            f"<code>{html.escape(out3[:200])}</code>",
        )


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 20 lines of a service's logs."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    args = (ctx.args or []) if ctx else []
    if not args:
        await _reply._reply(
            update,
            "\U0001f4dd <b>Usage:</b> <code>/logs &lt;service&gt;</code>\n"
            f"Services: {', '.join(_RMI_SERVICES)}",
        )
        return

    svc = args[0].strip().lower()
    if svc not in _RMI_SERVICES:
        await _reply._reply(update, _unknown_service(svc))
        return

    # Docker logs first.
    rc, out = _exec._run(["docker", "logs", "--tail", str(_LOG_LINES), svc])
    if rc == 0 and out:
        body = "\n".join(out.splitlines()[-_LOG_LINES:])
        await _reply._reply(
            update,
            f"\U0001f4dd <b>Logs: {svc}</b> (Docker)\n<code>{html.escape(body)}</code>",
        )
        return

    # Fall back to journalctl.
    rc2, out2 = _exec._run(["journalctl", "-u", svc, "-n", str(_LOG_LINES), "--no-pager"])
    if rc2 == 0 and out2:
        body = "\n".join(out2.splitlines()[-_LOG_LINES:])
        await _reply._reply(
            update,
            f"\U0001f4dd <b>Logs: {svc}</b> (systemd)\n<code>{html.escape(body)}</code>",
        )
        return

    await _reply._reply(
        update,
        f"\u274c No logs found for <code>{html.escape(svc)}</code>.",
    )
