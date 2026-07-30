#!/usr/bin/env python3
"""
RMI Admin Deploy — Deployment pipeline
========================================
Git ops, Docker rebuild, service orchestration.
Progress tracking, rollback capability.
"""

import os
import re
import shlex
import subprocess
import time
from datetime import UTC, datetime

import admin_ops

# ══════════════════════════════════════════════
# SERVICE DEFINITIONS
# ══════════════════════════════════════════════

SERVICES = {
    "backend": {
        "name": "RMI Backend API",
        "repo": os.getenv("BACKEND_REPO_DIR", "/opt/rmi-backend"),
        "compose_dir": os.getenv("BACKEND_COMPOSE_DIR", "/opt/rmi-backend"),
        "compose_service": "rmi-backend",
        "dockerfile": "Dockerfile",
    },
    "bot": {
        "name": "RugMunchBot",
        "repo": os.getenv("BOT_REPO_DIR", "/opt/rugmunchbot"),
        "compose_dir": os.getenv("BOT_COMPOSE_DIR", "/opt/rugmunchbot/bot"),
        "compose_service": "rugmunchbot",
        "dockerfile": "Dockerfile",
    },
    "admin": {
        "name": "RMI Admin Bot",
        "repo": os.getenv("BOT_REPO_DIR", "/opt/rugmunchbot"),
        "compose_dir": os.getenv("BOT_COMPOSE_DIR", "/opt/rugmunchbot/bot"),
        "compose_service": "rmi-admin-bot",
        "dockerfile": "Dockerfile",
    },
    "infra": {
        "name": "Fleet Infrastructure",
        "repo": os.getenv("FLEET_INFRA_DIR", "/opt/fleet-infra"),
        "compose_dir": os.getenv("FLEET_INFRA_COMPOSE", "/opt/fleet-infra/rmi-infra"),
        "compose_service": None,  # Full stack
        "dockerfile": None,
    },
}

# ══════════════════════════════════════════════
# GIT OPERATIONS
# ══════════════════════════════════════════════

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from output."""
    return _ANSI_RE.sub("", text)


def _clean_output(result: dict) -> dict:
    """Strip ANSI codes from a run_shell result dict."""
    result["output"] = _strip_ansi(result["output"])
    return result


def _git_cmd(repo_path: str, git_args: str, timeout: int = 30) -> dict:
    """Run a git command robustly with clean environment (no shell rc pollution).
    Uses shell=False + shlex.split() to prevent shell injection."""
    start = time.time()
    try:
        args_list = ["git", "-C", repo_path, "--no-pager"] + shlex.split(git_args)
        result = subprocess.run(
            args_list,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": os.path.expanduser("~"),
            },
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        output = _strip_ansi(output)
        if len(output) > 3500:
            output = output[:3400] + "\n\n[... truncated]"
        return {
            "ok": result.returncode == 0,
            "output": output or "(no output)",
            "error": "",
            "duration": time.time() - start,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "error": f"Git command timed out after {timeout}s",
            "duration": timeout,
        }
    except Exception as e:
        return {
            "ok": False,
            "output": "",
            "error": f"Git error: {str(e)[:200]}",
            "duration": time.time() - start,
        }


def git_status(repo_path: str) -> dict:
    """Get git status for a repo."""
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return {
            "ok": False,
            "output": "Not a git repository",
            "branch": "?",
            "changes": 0,
        }

    branch = _git_cmd(repo_path, "branch --show-current", timeout=5)
    status = _git_cmd(repo_path, "status --porcelain", timeout=5)
    behind = _git_cmd(
        repo_path,
        "rev-list --count HEAD..@{upstream} 2>/dev/null || echo 0",
        timeout=10,
    )
    ahead = _git_cmd(
        repo_path,
        "rev-list --count @{upstream}..HEAD 2>/dev/null || echo 0",
        timeout=10,
    )

    changes = len([ln for ln in status["output"].split("\n") if ln.strip()])

    return {
        "ok": True,
        "branch": branch["output"].strip(),
        "changes": changes,
        "behind": int(behind["output"].strip() or 0),
        "ahead": int(ahead["output"].strip() or 0),
        "status": status["output"][:1000],
    }


def git_pull(repo_path: str) -> dict:
    """Pull latest changes."""
    return _git_cmd(repo_path, "pull", timeout=30)


def git_log(repo_path: str, n: int = 5) -> str:
    """Get recent commits."""
    result = _git_cmd(repo_path, f"log --oneline -{n}", timeout=10)
    return result["output"]


# ══════════════════════════════════════════════
# DEPLOY PIPELINE
# ══════════════════════════════════════════════


class DeployStatus:
    """Tracks deploy progress for streaming updates."""

    def __init__(self, service: str):
        self.service = service
        self.steps: list[dict] = []
        self.started = datetime.now(UTC)
        self.complete = False

    def add_step(self, name: str, ok: bool, output: str = ""):
        self.steps.append(
            {
                "name": name,
                "ok": ok,
                "output": output[:500],
                "time": datetime.now(UTC),
            }
        )

    def format(self) -> str:
        lines = [f"\U0001f680 *Deploy: {self.service}*", ""]
        for s in self.steps:
            emoji = "\u2705" if s["ok"] else "\u274c" if not s["ok"] else "\u23f3"
            lines.append(f"{emoji} {s['name']}")
            if s["output"]:
                lines.append(f"   `{s['output'][:80]}`")
        lines.append("")
        elapsed = (datetime.now(UTC) - self.started).total_seconds()
        status = "\u2705 Complete" if self.complete else "\u23f3 In progress..."
        lines.append(f"{status} ({elapsed:.0f}s)")
        return "\n".join(lines)


def deploy(service_id: str) -> DeployStatus:
    """Run full deploy pipeline for a service. Returns DeployStatus for progress display."""
    if service_id not in SERVICES:
        s = DeployStatus(service_id)
        s.add_step("Validation", False, f"Unknown service: {service_id}")
        s.complete = True
        return s

    svc = SERVICES[service_id]
    status = DeployStatus(svc["name"])

    # Step 1: Git pull
    result = git_pull(svc["repo"])
    status.add_step("Git Pull", result["ok"], result["output"][:100])
    if not result["ok"]:
        status.complete = True
        return status

    # Step 2: Git status
    gs = git_status(svc["repo"])
    status.add_step(
        "Git Status",
        gs["ok"],
        f"branch={gs['branch']}, changes={gs['changes']}, behind={gs['behind']}",
    )

    # Step 3: Docker build
    if svc["dockerfile"]:
        df_path = os.path.join(svc["compose_dir"], svc["dockerfile"])
        if os.path.exists(df_path):
            build_result = admin_ops.run_shell(
                f"cd {shlex.quote(svc['compose_dir'])} && docker compose build --no-cache {svc['compose_service']} 2>&1",
                timeout=180,
            )
            status.add_step(
                "Docker Build", build_result["ok"], build_result["output"][:100]
            )
            if not build_result["ok"]:
                status.complete = True
                return status
        else:
            status.add_step("Docker Build", True, "No Dockerfile \u2014 skipped")
    else:
        status.add_step("Docker Build", True, "No Dockerfile \u2014 skipped")

    # Step 4: Docker up
    if svc["compose_service"]:
        up_result = admin_ops.run_shell(
            f"cd {shlex.quote(svc['compose_dir'])} && docker compose up -d {svc['compose_service']} 2>&1",
            timeout=60,
        )
        status.add_step("Docker Up", up_result["ok"], up_result["output"][:100])
    else:
        # Full stack up
        up_result = admin_ops.run_shell(
            f"cd {shlex.quote(svc['compose_dir'])} && docker compose up -d 2>&1",
            timeout=120,
        )
        status.add_step("Docker Compose Up", up_result["ok"], up_result["output"][:100])

    # Step 5: Health check
    time.sleep(3)  # Wait for container to start
    if service_id in ("backend",):
        hc = admin_ops.run_shell(
            "curl -sf http://localhost:8000/health 2>&1 || echo 'FAIL'", timeout=5
        )
        ok = "healthy" in hc["output"].lower() or "ok" in hc["output"].lower()
        status.add_step("Health Check", ok, hc["output"][:100])
    else:
        status.add_step("Health Check", True, "No health endpoint defined")

    status.complete = True
    return status


# ══════════════════════════════════════════════
# ROLLBACK
# ══════════════════════════════════════════════


def rollback(service_id: str) -> dict:
    """Rollback: git reset to previous commit and redeploy."""
    if service_id not in SERVICES:
        return {"ok": False, "output": f"Unknown service: {service_id}"}

    svc = SERVICES[service_id]
    repo = svc["repo"]

    # Save current HEAD
    current = _git_cmd(repo, "rev-parse --short HEAD", timeout=5)

    # Reset to previous commit
    reset = _git_cmd(repo, "reset --hard HEAD~1", timeout=10)

    if reset["ok"]:
        return {
            "ok": True,
            "output": f"Rolled back from {current['output'].strip()} to previous commit.\nRe-deploy the service to apply.",
        }
    return {"ok": False, "output": f"Rollback failed: {reset['output']}"}


# ══════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════


def deploy_menu_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f5a5\ufe0f Backend API", callback_data="deploy_backend"
                ),
                InlineKeyboardButton("\U0001f916 Main Bot", callback_data="deploy_bot"),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f6e1\ufe0f Admin Bot", callback_data="deploy_admin"
                ),
                InlineKeyboardButton(
                    "\U0001f3d7\ufe0f Infra Stack", callback_data="deploy_infra"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4cb Git Status", callback_data="deploy_status"
                )
            ],
            [InlineKeyboardButton("\u2b05\ufe0f Main Menu", callback_data="main")],
        ]
    )


def git_status_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = []
    for sid, svc in SERVICES.items():
        buttons.append(
            [
                InlineKeyboardButton(
                    f"\U0001f4cb {svc['name']}", callback_data=f"deploy_git_{sid}"
                )
            ]
        )
    buttons.append([InlineKeyboardButton("\u2b05\ufe0f Back", callback_data="deploy")])
    return InlineKeyboardMarkup(buttons)
