#!/usr/bin/env python3
"""
RugMunchBot -- @rmi_admin_bot server-management command handlers.

A set of owner-only commands that turn the bot into a useful server
management tool. Every command:

  * is owner-only (checked against ``OWNER_IDS`` via ``is_owner``);
  * executes via subprocess (``docker exec`` / ``systemctl`` / ``journalctl``);
  * returns output as a formatted Telegram message (HTML);
  * times out after 10 seconds;
  * fails gracefully (logs the error, returns an error message).

Commands
--------
  /status    — server health: CPU, RAM, disk, Docker containers, systemd
               services, uptime
  /services  — list all RMI services with status (running/stopped)
  /restart   — restart a Docker container or systemd service (owner only)
  /logs      — show last 20 lines of a service's logs
  /deploy    — git pull + restart the service for a repo
  /backup    — trigger a manual backup of Postgres + ClickHouse
  /alerts    — show recent system alerts from the Redis queue
  /budget    — show GetXAPI budget remaining (calls + dollars)
  /sources   — show count of active content sources (RSS + Telegram + X)
  /help      — list all commands

The module is a package ``handlers.admin``; the public entry point is
``register_admin_commands(app)`` which wires every command into the
``Application``. Callers keep importing ``handlers.admin`` exactly as before.
"""

from __future__ import annotations

import asyncio
import html
import os
import subprocess
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode

from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.admin")

#: Hard timeout for every subprocess command (seconds). Commands that hang
#: past this are killed and reported as a timeout instead of blocking the bot.
_CMD_TIMEOUT = 10.0

#: Max log lines returned by /logs.
_LOG_LINES = 20

#: RMI service names (Docker containers / systemd units) surfaced by
#: /services and accepted by /restart + /logs. Kept explicit so an operator
#: can never restart an arbitrary container by accident.
_RMI_SERVICES: tuple[str, ...] = (
    "rmi-backend",
    "rmi-frontend",
    "rugmunchbot",
    "rugmunchbot-redis",
    "rmi-redis",
    "rmi-postgres",
    "rmi-clickhouse",
    "rmi-rag",
    "rmi-mcp-x402",
    "degenfeed",
    "pryscraper",
    "walletpress",
)

#: Repos that /deploy knows how to pull + restart (repo name -> service).
_DEPLOY_TARGETS: dict[str, str] = {
    "rmi-backend": "rmi-backend",
    "rmi-frontend": "rmi-frontend",
    "rugmunchbot": "rugmunchbot",
    "degenfeed": "degenfeed",
    "pryscraper": "pryscraper",
    "walletpress": "walletpress",
}

#: Repo checkout roots on the host (used by /deploy). Env-overridable so the
#: same module works in dev and production.
_REPO_ROOT = os.getenv("RMI_REPO_ROOT", "/srv/work/repos")

#: Backup script path (used by /backup). Env-overridable.
_BACKUP_SCRIPT = os.getenv("RMI_BACKUP_SCRIPT", "/root/scripts/db-backup-all.sh")


def _run(cmd: list[str]) -> tuple[int, str]:
    """Run a subprocess command with a 10s timeout.

    Returns ``(returncode, stdout+stderr)``. On timeout the process is killed
    and ``(124, "timeout")`` is returned. Never raises.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed binaries, operator-controlled args, shell=False
            cmd,
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT,
            shell=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except subprocess.TimeoutExpired:
        logger.warning("admin_cmd_timeout cmd=%s", " ".join(cmd)[:120])
        return 124, "Command timed out after 10s."
    except FileNotFoundError as exc:
        logger.warning("admin_cmd_missing_binary cmd=%s err=%s", " ".join(cmd)[:120], exc)
        return 127, f"Binary not found: {exc.filename}"
    except Exception as exc:
        logger.warning("admin_cmd_failed cmd=%s err=%s", " ".join(cmd)[:120], exc)
        return 1, f"Command failed: {exc}"


async def _run_async(cmd: list[str]) -> tuple[int, str]:
    """Run a subprocess command, awaiting it on the event loop.

    Subprocess.run blocks the event loop; for the few commands that may take
    a moment (git pull, backups) we run them in a thread executor so the bot
    keeps serving other updates. Same 10s timeout + graceful-failure contract
    as ``_run``.
    """
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _run, cmd)
    except Exception as exc:
        logger.warning("admin_cmd_async_failed cmd=%s err=%s", " ".join(cmd)[:120], exc)
        return 1, f"Command failed: {exc}"


def _deny(update: Update) -> bool:
    """Return True when the sender is NOT an owner (and log the attempt)."""
    user = update.effective_user
    if user is not None and is_owner(user.id):
        return False
    logger.warning(
        "admin_cmd_denied user=%s username=%s",
        user.id if user else None,
        user.username if user else None,
    )
    return True


async def _reply(update: Update, text: str) -> None:
    """Send a formatted HTML reply, failing open on any Telegram error."""
    msg = update.effective_message
    if msg is None:
        return
    try:
        await msg.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        logger.warning("admin_reply_failed err=%s", str(exc)[:120])
        try:
            await msg.reply_text(text)
        except Exception:
            logger.warning("admin_reply_plain_failed", exc_info=True)


# ── /status ────────────────────────────────────────────────────────────────


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show server health: CPU, RAM, disk, Docker containers, systemd, uptime."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    lines: list[str] = ["\U0001f4ca <b>Server Status</b>", "\u2500" * 20]

    # Uptime + load
    rc, out = _run(["uptime"])
    if rc == 0 and out:
        lines.append(f"\U0001f504 <b>Uptime:</b> <code>{html.escape(out)}</code>")

    # CPU + RAM (top -bn1)
    rc, out = _run(["top", "-bn1"])
    if rc == 0 and out:
        for line in out.splitlines():
            if line.startswith(("%Cpu", "MiB Mem", "KiB Mem")):
                lines.append(f"\U0001f4be <code>{html.escape(line.strip())}</code>")

    # Disk
    rc, out = _run(["df", "-h", "/"])
    if rc == 0 and out:
        for line in out.splitlines()[1:2]:
            parts = line.split()
            if len(parts) >= 5:
                lines.append(
                    f"\U0001f4bd <b>Disk /:</b> <code>{parts[1]}</code> used ({parts[4]} full)"
                )

    # Docker containers
    rc, out = _run(["docker", "ps", "--format", "{{.Names}}: {{.Status}}"])
    if rc == 0 and out:
        lines.append("\U0001f433 <b>Docker containers:</b>")
        for c in out.splitlines()[:12]:
            lines.append(f"  \u2022 <code>{html.escape(c)}</code>")
    else:
        lines.append("\U0001f433 <b>Docker:</b> <i>unavailable</i>")

    # systemd services
    rc, out = _run(["systemctl", "is-active", "rugmunchbot", "rmi-backend"])
    if rc == 0 and out:
        lines.append(f"\U0001f3e2 <b>systemd:</b> <code>{html.escape(out)}</code>")

    await _reply(update, "\n".join(lines))


# ── /services ──────────────────────────────────────────────────────────────


async def cmd_services(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List all RMI services with status (running/stopped)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    lines: list[str] = ["\U0001f3e2 <b>RMI Services</b>", "\u2500" * 20]

    for svc in _RMI_SERVICES:
        # Prefer Docker; fall back to systemd.
        rc, out = _run(["docker", "inspect", "-f", "{{.State.Status}}", svc])
        if rc == 0 and out:
            status = out.strip()
            emoji = "\u2705" if status == "running" else "\u26d4"
            lines.append(f"{emoji} <code>{svc}</code> — {status}")
            continue
        rc2, out2 = _run(["systemctl", "is-active", svc])
        if rc2 == 0:
            status = out2.strip()
            emoji = "\u2705" if status == "active" else "\u26d4"
            lines.append(f"{emoji} <code>{svc}</code> — {status}")
        else:
            lines.append(f"\u2753 <code>{svc}</code> — unknown")

    await _reply(update, "\n".join(lines))


# ── /restart <service> ─────────────────────────────────────────────────────


async def cmd_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart a Docker container or systemd service (owner only)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    args = (ctx.args or []) if ctx else []
    if not args:
        await _reply(
            update,
            "\U0001f504 <b>Usage:</b> <code>/restart &lt;service&gt;</code>\n"
            f"Services: {', '.join(_RMI_SERVICES)}",
        )
        return

    svc = args[0].strip().lower()
    if svc not in _RMI_SERVICES:
        await _reply(
            update,
            f"\u274c Unknown service <code>{html.escape(svc)}</code>.\n"
            f"Allowed: {', '.join(_RMI_SERVICES)}",
        )
        return

    # Try Docker first, then systemd.
    rc, out = _run(["docker", "inspect", "-f", "{{.State.Status}}", svc])
    if rc == 0 and out:
        rc2, out2 = _run(["docker", "restart", svc])
        if rc2 == 0:
            await _reply(update, f"\U0001f504 <b>{svc}</b> restarted (Docker).")
        else:
            await _reply(
                update,
                f"\u274c Failed to restart <code>{svc}</code> (Docker): "
                f"<code>{html.escape(out2[:200])}</code>",
            )
        return

    rc3, out3 = _run(["systemctl", "restart", svc])
    if rc3 == 0:
        await _reply(update, f"\U0001f504 <b>{svc}</b> restarted (systemd).")
    else:
        await _reply(
            update,
            f"\u274c Failed to restart <code>{svc}</code> (systemd): "
            f"<code>{html.escape(out3[:200])}</code>",
        )


# ── /logs <service> ────────────────────────────────────────────────────────


async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 20 lines of a service's logs."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    args = (ctx.args or []) if ctx else []
    if not args:
        await _reply(
            update,
            "\U0001f4dd <b>Usage:</b> <code>/logs &lt;service&gt;</code>\n"
            f"Services: {', '.join(_RMI_SERVICES)}",
        )
        return

    svc = args[0].strip().lower()
    if svc not in _RMI_SERVICES:
        await _reply(
            update,
            f"\u274c Unknown service <code>{html.escape(svc)}</code>.\n"
            f"Allowed: {', '.join(_RMI_SERVICES)}",
        )
        return

    # Docker logs first.
    rc, out = _run(["docker", "logs", "--tail", str(_LOG_LINES), svc])
    if rc == 0 and out:
        body = "\n".join(out.splitlines()[-_LOG_LINES:])
        await _reply(
            update,
            f"\U0001f4dd <b>Logs: {svc}</b> (Docker)\n<code>{html.escape(body)}</code>",
        )
        return

    # Fall back to journalctl.
    rc2, out2 = _run(["journalctl", "-u", svc, "-n", str(_LOG_LINES), "--no-pager"])
    if rc2 == 0 and out2:
        body = "\n".join(out2.splitlines()[-_LOG_LINES:])
        await _reply(
            update,
            f"\U0001f4dd <b>Logs: {svc}</b> (systemd)\n<code>{html.escape(body)}</code>",
        )
        return

    await _reply(
        update,
        f"\u274c No logs found for <code>{html.escape(svc)}</code>.",
    )


# ── /deploy <repo> ─────────────────────────────────────────────────────────


async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """git pull + restart the service for a repo (owner only)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    args = (ctx.args or []) if ctx else []
    if not args:
        await _reply(
            update,
            "\U0001f680 <b>Usage:</b> <code>/deploy &lt;repo&gt;</code>\n"
            f"Repos: {', '.join(_DEPLOY_TARGETS)}",
        )
        return

    repo = args[0].strip().lower()
    if repo not in _DEPLOY_TARGETS:
        await _reply(
            update,
            f"\u274c Unknown repo <code>{html.escape(repo)}</code>.\n"
            f"Allowed: {', '.join(_DEPLOY_TARGETS)}",
        )
        return

    svc = _DEPLOY_TARGETS[repo]
    repo_path = os.path.join(_REPO_ROOT, repo)

    await _reply(update, f"\U0001f680 Deploying <b>{repo}</b>...")

    # git pull
    rc, out = await _run_async(["git", "-C", repo_path, "pull", "--ff-only"])
    if rc != 0:
        await _reply(
            update,
            f"\u274c <b>git pull failed</b> for <code>{repo}</code>:\n"
            f"<code>{html.escape(out[:300])}</code>",
        )
        return

    # Restart the service (Docker first, then systemd).
    rc2, out2 = _run(["docker", "inspect", "-f", "{{.State.Status}}", svc])
    if rc2 == 0 and out2:
        rc3, out3 = _run(["docker", "restart", svc])
        if rc3 == 0:
            await _reply(
                update,
                f"\U0001f680 <b>{repo}</b> pulled + <b>{svc}</b> restarted (Docker).\n"
                f"<code>{html.escape(out[:200])}</code>",
            )
        else:
            await _reply(
                update,
                f"\u274c Pulled <code>{repo}</code> but restart failed: "
                f"<code>{html.escape(out3[:200])}</code>",
            )
        return

    rc4, out4 = _run(["systemctl", "restart", svc])
    if rc4 == 0:
        await _reply(
            update,
            f"\U0001f680 <b>{repo}</b> pulled + <b>{svc}</b> restarted (systemd).\n"
            f"<code>{html.escape(out[:200])}</code>",
        )
    else:
        await _reply(
            update,
            f"\u274c Pulled <code>{repo}</code> but restart failed: "
            f"<code>{html.escape(out4[:200])}</code>",
        )


# ── /backup ────────────────────────────────────────────────────────────────


async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger a manual backup of Postgres + ClickHouse (owner only)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    await _reply(update, "\U0001f4e6 Triggering manual backup (Postgres + ClickHouse)...")

    rc, out = await _run_async(["bash", _BACKUP_SCRIPT])
    if rc == 0:
        await _reply(
            update,
            f"\u2705 <b>Backup complete.</b>\n<code>{html.escape(out[:400])}</code>",
        )
    else:
        await _reply(
            update,
            f"\u274c <b>Backup failed</b> (rc={rc}):\n<code>{html.escape(out[:400])}</code>",
        )


# ── /alerts ────────────────────────────────────────────────────────────────


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent system alerts from the Redis queue (owner only)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    try:
        import redis.asyncio as aioredis

        from rugmunchbot._vendor.alert_pipeline import (
            REDIS_HOST,
            REDIS_PORT,
            REDIS_PW,
            SYSTEM_ALERT_QUEUE_KEY,
        )

        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PW or None,
            decode_responses=True,
            protocol=2,
        )
        raw = await r.lrange(SYSTEM_ALERT_QUEUE_KEY, 0, 9)
        await r.close()
    except Exception as exc:
        logger.warning("admin_alerts_redis_failed err=%s", str(exc)[:120])
        await _reply(update, "\u274c Could not read alerts from Redis.")
        return

    if not raw:
        await _reply(update, "\U0001f4e6 <b>No system alerts</b> in the queue.")
        return

    import json

    lines: list[str] = ["\U0001f4e6 <b>Recent System Alerts</b>", "\u2500" * 20]
    for entry in raw:
        try:
            item = json.loads(entry)
        except (json.JSONDecodeError, TypeError):
            continue
        sev = item.get("severity", "info")
        emoji = {
            "critical": "\U0001f534",
            "high": "\U0001f7e0",
            "medium": "\U0001f7e1",
            "low": "\U0001f7e2",
        }.get(sev, "\u26aa")
        title = html.escape(str(item.get("title") or "alert"))
        desc = html.escape(str(item.get("description") or ""))[:120]
        lines.append(f"{emoji} <b>{title}</b>\n  {desc}")
        if len(lines) >= 12:
            break

    await _reply(update, "\n".join(lines))


# ── /budget ────────────────────────────────────────────────────────────────


async def cmd_budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show GetXAPI budget remaining (calls + dollars) (owner only)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    try:
        from rugmunchbot.tools.getxapi_budget import get_daily_usage
        from rugmunchbot.tools.getxapi_config import (
            GETXAPI_DAILY_CALL_LIMIT,
            GETXAPI_TOTAL_BUDGET,
        )

        usage = get_daily_usage()
    except Exception as exc:
        logger.warning("admin_budget_failed err=%s", str(exc)[:120])
        await _reply(update, "\u274c GetXAPI budget check unavailable.")
        return

    lines = [
        "\U0001f4b0 <b>GetXAPI Budget</b>",
        "\u2500" * 20,
        f"\U0001f4c8 Calls today: <b>{usage['calls']}</b> / {GETXAPI_DAILY_CALL_LIMIT}",
        f"\U0001f4b5 Spent: <b>${usage['spend']:.4f}</b> of ${GETXAPI_TOTAL_BUDGET:.2f}",
        f"\U0001f4b0 Remaining: <b>${usage['budget_remaining']:.4f}</b>",
        f"\U0001f4a1 Calls remaining: <b>{usage['calls_remaining']}</b>",
    ]
    await _reply(update, "\n".join(lines))


# ── /sources ───────────────────────────────────────────────────────────────


async def cmd_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show count of active content sources (RSS + Telegram + X) (owner only)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    # Best-effort counts from the DB (fail-open to 0 on any error).
    rss = tg = x = 0
    try:
        from rugmunchbot import db

        conn = db.get_db()
        try:
            rss = conn.execute(
                "SELECT COUNT(*) AS c FROM content_sources WHERE kind = 'rss' AND active = 1"
            ).fetchone()["c"]
        except Exception:
            rss = 0
        try:
            tg = conn.execute(
                "SELECT COUNT(*) AS c FROM content_sources WHERE kind = 'telegram' AND active = 1"
            ).fetchone()["c"]
        except Exception:
            tg = 0
        try:
            x = conn.execute(
                "SELECT COUNT(*) AS c FROM content_sources WHERE kind = 'x' AND active = 1"
            ).fetchone()["c"]
        except Exception:
            x = 0
        conn.close()
    except Exception as exc:
        logger.warning("admin_sources_db_failed err=%s", str(exc)[:120])

    # GetXAPI monitored accounts count as X sources.
    try:
        from rugmunchbot.tools.getxapi_config import GETXAPI_SECURITY_ACCOUNTS

        x = max(x, len(GETXAPI_SECURITY_ACCOUNTS))
    except Exception as exc:
        logger.debug("admin_sources_getxapi_failed err=%s", str(exc)[:120])

    lines = [
        "\U0001f4e1 <b>Active Content Sources</b>",
        "\u2500" * 20,
        f"\U0001f4f0 RSS: <b>{rss}</b>",
        f"\U0001f4ac Telegram: <b>{tg}</b>",
        f"\U0001f4e2 X/Twitter: <b>{x}</b>",
        f"\U0001f4ca Total: <b>{rss + tg + x}</b>",
    ]
    await _reply(update, "\n".join(lines))


# ── /help ──────────────────────────────────────────────────────────────────


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List all admin commands (owner only)."""
    if _deny(update):
        await _reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    lines = [
        "\U0001f6e1\ufe0f <b>@rmi_admin_bot — Server Management</b>",
        "\u2500" * 20,
        "/status — server health (CPU, RAM, disk, Docker, systemd, uptime)",
        "/services — list all RMI services with status",
        "/restart &lt;service&gt; — restart a Docker container or systemd service",
        "/logs &lt;service&gt; — last 20 lines of a service's logs",
        "/deploy &lt;repo&gt; — git pull + restart the service for a repo",
        "/backup — manual backup of Postgres + ClickHouse",
        "/alerts — recent system alerts from the Redis queue",
        "/budget — GetXAPI budget remaining (calls + dollars)",
        "/sources — active content sources (RSS + Telegram + X)",
        "/help — this list",
        "",
        f"Services: {', '.join(_RMI_SERVICES)}",
        f"Repos: {', '.join(_DEPLOY_TARGETS)}",
    ]
    await _reply(update, "\n".join(lines))


# ── Registration ───────────────────────────────────────────────────────────


def register_admin_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register every @rmi_admin_bot command on *app*.

    Uses the same ``CommandHandler`` wiring as the rest of the bot. Each
    command is owner-gated inside its handler (defense-in-depth) and hidden
    from the BotFather menu (they are not added to ``_COMMAND_SECTIONS``).
    """
    from telegram.ext import CommandHandler

    for name, handler in (
        ("status", cmd_status),
        ("services", cmd_services),
        ("restart", cmd_restart),
        ("logs", cmd_logs),
        ("deploy", cmd_deploy),
        ("backup", cmd_backup),
        ("alerts", cmd_alerts),
        ("budget", cmd_budget),
        ("sources", cmd_sources),
        ("help", cmd_help),
    ):
        app.add_handler(CommandHandler(name, handler))


__all__ = [
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
    "register_admin_commands",
]
