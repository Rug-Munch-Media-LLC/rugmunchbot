#!/usr/bin/env python3
"""
RMI Admin Docker — Container management panel
===============================================
Interactive Docker control with inline keyboards.
Import from admin_ops for raw execution.

Callback data conventions (wired in admin_bot.py):
  docker_detail_{name}     Container detail view
  docker_logs_{name}       Container logs (50 lines)
  docker_stats             All containers stats
  docker_stats_{name}      Single container stats
  docker_restart_{name}    Restart container
  docker_stop_{name}       Stop container
  docker_start_{name}      Start container
  docker_list_all          Show all containers (incl. stopped)
  docker_disk              Docker disk usage
  compose_ps               Compose process list
  compose_restart          Compose restart
"""

from __future__ import annotations

import os
import shlex
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import InlineKeyboardMarkup

# Import our shell executor
import admin_ops

# ══════════════════════════════════════════════
# CONTAINER DISCOVERY
# ══════════════════════════════════════════════


def list_containers(all_containers: bool = False) -> list[dict]:
    """Return structured container list with status metadata.

    Each entry: {name, status, image, ports, state, healthy}
    Healthy key maps state → emoji: 🟢 running, 🔴 exited, 🟡 restarting, ⚫ other.
    """
    flag = "-a" if all_containers else ""
    result = admin_ops.run_shell(
        f"docker ps {flag} --format '{{{{.Names}}}}|{{{{.Status}}}}|{{{{.Image}}}}|{{{{.Ports}}}}|{{{{.State}}}}' --no-trunc",
        timeout=10,
    )
    containers = []
    for line in result["output"].split("\n"):
        if "|" not in line:
            continue
        parts = line.split("|", 4)
        if len(parts) >= 5:
            state = parts[4].strip()
            healthy = (
                "🟢"
                if state == "running"
                else "🔴"
                if state == "exited"
                else "🟡"
                if state == "restarting"
                else "⚫"
            )
            containers.append(
                {
                    "name": parts[0].strip(),
                    "status": parts[1].strip()[:50],
                    "image": parts[2].strip()[:40],
                    "ports": parts[3].strip()[:40],
                    "state": state,
                    "healthy": healthy,
                }
            )
    return containers


def get_container(name: str) -> dict | None:
    """Get single container details by name or partial match.

    Returns dict with keys: name, state, started, image, restart
    or None if container does not exist.
    """
    result = admin_ops.run_shell(
        f"docker inspect {shlex.quote(name)} --format '{{{{.Name}}}}|{{{{.State.Status}}}}|{{{{.State.StartedAt}}}}|{{{{.Config.Image}}}}|{{{{.HostConfig.RestartPolicy.Name}}}}' 2>&1",
        timeout=10,
    )
    if not result["ok"] or "No such object" in result["output"]:
        return None
    parts = result["output"].split("|")
    return {
        "name": parts[0].strip().lstrip("/") if len(parts) > 0 else "?",
        "state": parts[1].strip() if len(parts) > 1 else "?",
        "started": parts[2].strip() if len(parts) > 2 else "?",
        "image": parts[3].strip() if len(parts) > 3 else "?",
        "restart": parts[4].strip() if len(parts) > 4 else "?",
    }


# ══════════════════════════════════════════════
# CONTAINER ACTIONS
# ══════════════════════════════════════════════


def restart_container(name: str) -> dict:
    """Restart a container with validation. Returns admin_ops result dict."""
    container = get_container(name)
    if not container:
        return {"ok": False, "output": f"Container '{name}' not found", "error": ""}
    return admin_ops.run_shell(f"docker restart {shlex.quote(name)} 2>&1", timeout=30)


def stop_container(name: str) -> dict:
    """Stop a container with validation. Returns admin_ops result dict."""
    container = get_container(name)
    if not container:
        return {"ok": False, "output": f"Container '{name}' not found", "error": ""}
    return admin_ops.run_shell(f"docker stop {shlex.quote(name)} 2>&1", timeout=30)


def start_container(name: str) -> dict:
    """Start a stopped container with validation. Returns admin_ops result dict."""
    container = get_container(name)
    if not container:
        return {"ok": False, "output": f"Container '{name}' not found", "error": ""}
    return admin_ops.run_shell(f"docker start {shlex.quote(name)} 2>&1", timeout=30)


def get_logs(name: str, tail: int = 50) -> dict:
    """Get container logs. Falls back to partial name match.

    ``tail`` controls the number of recent log lines (default 50, max 500).
    Returns admin_ops result dict with output truncated to 3500 chars.
    """
    container = get_container(name)
    if not container:
        # Try running container by partial name match
        result = admin_ops.run_shell(
            f"docker ps --filter name={shlex.quote(name)} -q", timeout=5
        )
        if not result["output"].strip():
            return {
                "ok": False,
                "output": "",
                "error": f"Container '{name}' not running",
            }
    return admin_ops.run_shell(
        f"docker logs --tail {tail} {shlex.quote(name)} 2>&1", timeout=15
    )


def get_stats(name: str = "") -> dict:
    """Get live resource stats for one or all containers.

    Returns CPU%, memory usage/net I/O table via ``docker stats --no-stream``.
    """
    target = shlex.quote(name) if name else ""
    return admin_ops.run_shell(
        f"docker stats --no-stream {target} --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}' 2>&1",
        timeout=15,
    )


def exec_in_container(name: str, cmd: str) -> dict:
    """Execute an arbitrary command inside a running container.

    Validates container exists and is running before execution.
    Returns admin_ops result dict.
    """
    container = get_container(name)
    if not container:
        return {"ok": False, "output": "", "error": f"Container '{name}' not found"}
    if container["state"] != "running":
        return {
            "ok": False,
            "output": "",
            "error": f"Container '{name}' is {container['state']}, not running",
        }
    return admin_ops.run_shell(
        f"docker exec {shlex.quote(name)} {cmd} 2>&1", timeout=15
    )


# ══════════════════════════════════════════════
# COMPOSE MANAGEMENT
# ══════════════════════════════════════════════

COMPOSE_FILES: dict[str, str | None] = {
    "infra": "/srv/work/repos/fleet-infra/rmi-infra/docker-compose.infrastructure.yml",
    "backend": (
        "/srv/work/repos/rmi-backend/docker-compose.yml"
        if os.path.exists("/srv/work/repos/rmi-backend/docker-compose.yml")
        else None
    ),
}


def compose_ps(project: str = "infra") -> dict:
    """List containers in a docker-compose project.

    Supported projects: ``infra`` (default), ``backend``.
    Returns admin_ops result dict.
    """
    if project not in COMPOSE_FILES or not COMPOSE_FILES[project]:
        return {"ok": False, "output": "", "error": f"No compose file for '{project}'"}
    f = COMPOSE_FILES[project]
    d = os.path.dirname(f)
    return admin_ops.run_shell(
        f"cd {shlex.quote(d)} && docker compose -f {shlex.quote(f)} ps 2>&1",
        timeout=15,
    )


def compose_restart(project: str = "infra") -> dict:
    """Restart all services in a docker-compose project.

    Uses ``docker compose restart`` (graceful, no downtime).
    Returns admin_ops result dict.
    """
    if project not in COMPOSE_FILES or not COMPOSE_FILES[project]:
        return {"ok": False, "output": "", "error": f"No compose file for '{project}'"}
    f = COMPOSE_FILES[project]
    d = os.path.dirname(f)
    return admin_ops.run_shell(
        f"cd {shlex.quote(d)} && docker compose -f {shlex.quote(f)} restart 2>&1",
        timeout=60,
    )


def compose_up(project: str = "infra") -> dict:
    """Bring up all services in a docker-compose project in detached mode.

    Returns admin_ops result dict (may take up to 120s for large stacks).
    """
    if project not in COMPOSE_FILES or not COMPOSE_FILES[project]:
        return {"ok": False, "output": "", "error": f"No compose file for '{project}'"}
    f = COMPOSE_FILES[project]
    d = os.path.dirname(f)
    return admin_ops.run_shell(
        f"cd {shlex.quote(d)} && docker compose -f {shlex.quote(f)} up -d 2>&1",
        timeout=120,
    )


# ══════════════════════════════════════════════
# RESOURCE OVERVIEW
# ══════════════════════════════════════════════


def docker_resource_summary() -> str:
    """Get a quick resource summary table of all running containers.

    Returns formatted table with Name, CPU%, Mem%, MemUsage columns.
    """
    result = admin_ops.run_shell(
        "docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}\t{{.MemUsage}}' 2>&1",
        timeout=15,
    )
    return result["output"]


def docker_disk_usage() -> str:
    """Show Docker disk usage summary (images, containers, volumes, build cache)."""
    result = admin_ops.run_shell("docker system df 2>&1", timeout=15)
    return result["output"]


# ══════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════


def container_list_keyboard(
    all_containers: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    """Generate a paginated container list with per-container action buttons.

    Returns ``(message_text, InlineKeyboardMarkup)``.
    Message includes a summary header (running/total count).
    First 8 containers get individual detail buttons; common actions follow.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    containers = list_containers(all_containers)

    # Text summary
    running = sum(1 for c in containers if c["state"] == "running")
    lines = [f"🐳 *Docker Containers* ({running}/{len(containers)} running)\n"]
    for c in containers:
        lines.append(f"{c['healthy']} `{c['name']}` — {c['status']}")

    # Keyboard: first 8 containers get individual buttons, then common actions
    buttons = []
    for c in containers[:8]:
        name_short = c["name"][:25]
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{c['healthy']} {name_short}",
                    callback_data=f"docker_detail_{c['name']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton("📊 All Stats", callback_data="docker_stats"),
            InlineKeyboardButton("📋 Compose PS", callback_data="compose_ps"),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton("🔄 Compose Restart", callback_data="compose_restart"),
            InlineKeyboardButton("💾 Disk Usage", callback_data="docker_disk"),
        ]
    )
    if not all_containers:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Show All (incl. stopped)", callback_data="docker_list_all"
                )
            ]
        )
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])

    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def container_detail_keyboard(name: str) -> tuple[str, InlineKeyboardMarkup]:
    """Generate a detail view for a specific container with action buttons.

    Returns ``(message_text, InlineKeyboardMarkup)``.
    Shows: name, state, image, started time, restart policy.
    Action row: logs, stats, restart, stop, start.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    container = get_container(name)
    if not container:
        return (
            f"❌ Container '{name}' not found",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="docker")]]
            ),
        )

    # Compute uptime for running containers
    started_raw = container["started"]
    uptime_str = ""
    if started_raw and started_raw != "?" and container["state"] == "running":
        try:
            started_dt = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
            delta = datetime.now().astimezone() - started_dt
            days = delta.days
            hours, remainder = divmod(delta.seconds, 3600)
            minutes = remainder // 60
            if days > 0:
                uptime_str = f"\nUptime: `{days}d {hours}h {minutes}m`"
            else:
                uptime_str = f"\nUptime: `{hours}h {minutes}m`"
        except (ValueError, TypeError):
            pass

    lines = [
        f"🐳 *{name}*",
        "",
        f"State: `{container['state']}`{uptime_str}",
        f"Image: `{container['image']}`",
        f"Started: `{started_raw[:30]}`",
        f"Restart Policy: `{container['restart']}`",
    ]

    buttons = [
        [
            InlineKeyboardButton("📋 Logs (50)", callback_data=f"docker_logs_{name}"),
            InlineKeyboardButton("📊 Stats", callback_data=f"docker_stats_{name}"),
        ],
        [
            InlineKeyboardButton("🔄 Restart", callback_data=f"docker_restart_{name}"),
            InlineKeyboardButton("⏹ Stop", callback_data=f"docker_stop_{name}"),
        ],
        [InlineKeyboardButton("▶️ Start", callback_data=f"docker_start_{name}")],
        [InlineKeyboardButton("⬅️ Back to List", callback_data="docker")],
    ]

    return "\n".join(lines), InlineKeyboardMarkup(buttons)
