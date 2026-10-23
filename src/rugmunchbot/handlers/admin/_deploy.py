"""Long-running operator commands: /deploy and /backup."""

from __future__ import annotations

import html
import os
from typing import TYPE_CHECKING

from rugmunchbot.handlers.admin import _exec, _reply
from rugmunchbot.handlers.admin._constants import (
    _BACKUP_SCRIPT,
    _DEPLOY_TARGETS,
    _REPO_ROOT,
)

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def _restart_service(update: Update, svc: str, repo: str, pull_out: str) -> None:
    """Restart *svc* after a successful pull, preferring Docker over systemd."""
    rc, out = _exec._run(["docker", "inspect", "-f", "{{.State.Status}}", svc])
    if rc == 0 and out:
        rc_restart, restart_out = _exec._run(["docker", "restart", svc])
        backend = "Docker"
    else:
        rc_restart, restart_out = _exec._run(["systemctl", "restart", svc])
        backend = "systemd"

    if rc_restart != 0:
        await _reply._reply(
            update,
            f"\u274c Pulled <code>{repo}</code> but restart failed: "
            f"<code>{html.escape(restart_out[:200])}</code>",
        )
        return

    await _reply._reply(
        update,
        f"\U0001f680 <b>{repo}</b> pulled + <b>{svc}</b> restarted ({backend}).\n"
        f"<code>{html.escape(pull_out[:200])}</code>",
    )


async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """git pull + restart the service for a repo (owner only)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    args = (ctx.args or []) if ctx else []
    if not args:
        await _reply._reply(
            update,
            "\U0001f680 <b>Usage:</b> <code>/deploy &lt;repo&gt;</code>\n"
            f"Repos: {', '.join(_DEPLOY_TARGETS)}",
        )
        return

    repo = args[0].strip().lower()
    if repo not in _DEPLOY_TARGETS:
        await _reply._reply(
            update,
            f"\u274c Unknown repo <code>{html.escape(repo)}</code>.\n"
            f"Allowed: {', '.join(_DEPLOY_TARGETS)}",
        )
        return

    svc = _DEPLOY_TARGETS[repo]
    repo_path = os.path.join(_REPO_ROOT, repo)

    await _reply._reply(update, f"\U0001f680 Deploying <b>{repo}</b>...")

    rc, out = await _exec._run_async(["git", "-C", repo_path, "pull", "--ff-only"])
    if rc != 0:
        await _reply._reply(
            update,
            f"\u274c <b>git pull failed</b> for <code>{repo}</code>:\n"
            f"<code>{html.escape(out[:300])}</code>",
        )
        return

    await _restart_service(update, svc, repo, out)


async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger a manual backup of Postgres + ClickHouse (owner only)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    await _reply._reply(update, "\U0001f4e6 Triggering manual backup (Postgres + ClickHouse)...")

    rc, out = await _exec._run_async(["bash", _BACKUP_SCRIPT])
    if rc == 0:
        await _reply._reply(
            update,
            f"\u2705 <b>Backup complete.</b>\n<code>{html.escape(out[:400])}</code>",
        )
    else:
        await _reply._reply(
            update,
            f"\u274c <b>Backup failed</b> (rc={rc}):\n<code>{html.escape(out[:400])}</code>",
        )
