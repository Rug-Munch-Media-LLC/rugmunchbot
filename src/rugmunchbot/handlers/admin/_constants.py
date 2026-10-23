"""Configuration constants for the @rmi_admin_bot command handlers."""

from __future__ import annotations

import os

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
    "rugbrain-api",
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
