#!/usr/bin/env python3
"""
RMI Admin Operations — Server Management Backend
=================================================
Pure functions for shell execution, Docker management,
system monitoring, backup control, and deployment.
No Telegram dependencies — callable from anywhere.
"""

import os
import shlex
import subprocess
import time
from datetime import UTC, datetime

# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════

BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf .",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",
    "> /dev/sda",
    "> /dev/vda",
    "> /dev/nvme",
    "chmod 777 /",
    "chmod -R 777 /",
]

# Whitelist for user-facing shell commands (/sh, terminal mode).
# Only commands matching these prefixes are allowed when shell=True is unsafe.
# Uses shlex.split() + shell=False to prevent shell injection.
SAFE_CMD_PREFIXES = [
    # System info
    "hostname",
    "uptime",
    "df",
    "free",
    "nproc",
    "uname",
    "date",
    "cat /proc/",
    "ls /backups",
    "ls -lth /backups",
    "ls -lt /backups",
    # Process info
    "ps aux",
    "ps -",
    # Network
    "ss ",
    "netstat",
    # Disk usage
    "du -sh ",
    # Docker read-only
    "docker ps",
    "docker stats",
    "docker system df",
    "docker compose ps",
    "docker compose config",
    "docker compose -f",
    "docker inspect",
    "docker logs",
    # Docker service management (restricted to known services)
    "docker restart ",
    "docker stop ",
    "docker start ",
    # Systemd status (read-only)
    "systemctl status",
    "systemctl is-active",
    # Journal logs
    "journalctl -u ",
    # Health curl
    "curl -sf",
    "curl -s",
    # Finding files
    "find ",
    # Docker exec for health checks only (use 'docker exec rmi-')
    "docker exec rmi-",
]

# Compose paths
COMPOSE_INFRA = (
    "/srv/work/repos/fleet-infra/rmi-infra/docker-compose.infrastructure.yml"
)
COMPOSE_BACKEND_DIR = "/srv/work/repos/rmi-backend"
LOG_FILE = "/var/log/rmi-admin.log"

KNOWN_SERVICES = [
    "rmi-backend",
    "rugmunchbot",
    "rmi-postgres",
    "rmi-redis",
    "rmi-clickhouse",
    "rmi-neo4j",
    "rmi-qdrant",
    "rmi-minio",
    "fleet-prometheus",
    "fleet-grafana",
    "hermes-gateway",
    "rmi-mcp-x402",
    "rmi-litellm",
    "rmi-n8n",
]

# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════


def _log(cmd: str, user: str = "admin"):
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] user={user} cmd={shlex.quote(cmd)[:200]}\n")
    except Exception:
        pass


def _sanitize(text: str) -> str:
    """Strip potential secrets from output."""
    for line in text.split("\n"):
        upper = line.upper()
        if any(
            kw in upper
            for kw in ["PASSWORD", "SECRET", "TOKEN=", "API_KEY=", "PRIVATE"]
        ):
            text = text.replace(line, "[REDACTED]")
    return text


def format_bytes(num_bytes: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f}P"


# ══════════════════════════════════════════════
# SHELL EXECUTION
# ══════════════════════════════════════════════


def check_dangerous(cmd: str) -> bool:
    """Return True if command matches any blocked pattern."""
    cmd_lower = cmd.lower().replace(" ", "")
    for pattern in BLOCKED_PATTERNS:
        if pattern.lower().replace(" ", "") in cmd_lower:
            return True
    return False


def _match_prefix(cmd: str, prefixes: list[str]) -> bool:
    """Check if cmd starts with any of the given prefixes (case-insensitive)."""
    cmd_lower = cmd.lower().strip()
    for p in prefixes:
        if cmd_lower.startswith(p.lower()):
            return True
    return False


def is_safe_command(cmd: str) -> bool:
    """Validate a user-supplied command against the SAFE_CMD_PREFIXES whitelist.
    Returns True only if the command matches an allowed prefix pattern.
    This prevents shell injection by restricting to a known-safe set.
    """
    return _match_prefix(cmd, SAFE_CMD_PREFIXES)


def run_shell(cmd: str, timeout: int = 30) -> dict:
    """Execute a shell command. Returns {ok, output, error, duration}.
    NOTE: For INTERNAL use only (pipes, redirects, shell features).
    User-facing commands MUST use run_shell_safe() instead.
    """
    _log(cmd)
    start = time.time()

    if check_dangerous(cmd):
        return {
            "ok": False,
            "output": "",
            "error": "⛔ BLOCKED: Command matches dangerous pattern. Not executed.",
            "duration": time.time() - start,
        }

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            timeout=timeout,
            capture_output=True,
            text=True,
            executable="/bin/bash",
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            output += "\n" + result.stderr.strip()
        output = _sanitize(output)
        if len(output) > 3500:
            output = output[:3400] + "\n\n[... truncated at 3500 chars]"
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
            "error": f"⏰ Command timed out after {timeout}s",
            "duration": timeout,
        }
    except Exception as e:
        return {
            "ok": False,
            "output": "",
            "error": f"❌ Error: {str(e)[:200]}",
            "duration": time.time() - start,
        }


def run_shell_safe(cmd: str, timeout: int = 30) -> dict:
    """Execute a user-supplied command SAFELY.
    Uses shlex.split() + shell=False to prevent shell injection.
    Validates against SAFE_CMD_PREFIXES whitelist.
    Returns {ok, output, error, duration}.
    """
    _log(cmd)
    start = time.time()

    # Whitelist check — reject commands not in the safe list
    if not is_safe_command(cmd):
        return {
            "ok": False,
            "output": "",
            "error": (
                "⛔ Command not allowed. Use one of:\n"
                "  • system, df, free, uptime, docker ps/stats/logs\n"
                "  • systemctl status <service>\n"
                "  • journalctl -u <service>\n"
                "  • docker restart|stop|start <container>\n"
                "  • ps aux, ss, netstat, du -sh\n"
                "  • docker compose ps/config\n"
                "  • curl -sf <url>\n"
                "  • find <path>\n\n"
                "Run /sh with no args for full help."
            ),
            "duration": time.time() - start,
        }

    # Block dangerous patterns even within allowed commands
    if check_dangerous(cmd):
        return {
            "ok": False,
            "output": "",
            "error": "⛔ BLOCKED: Command matches dangerous pattern. Not executed.",
            "duration": time.time() - start,
        }

    try:
        args = shlex.split(cmd)
        result = subprocess.run(
            args,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            output += "\n" + result.stderr.strip()
        output = _sanitize(output)
        if len(output) > 3500:
            output = output[:3400] + "\n\n[... truncated at 3500 chars]"
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
            "error": f"⏰ Command timed out after {timeout}s",
            "duration": timeout,
        }
    except Exception as e:
        return {
            "ok": False,
            "output": "",
            "error": f"❌ Error: {str(e)[:200]}",
            "duration": time.time() - start,
        }


# ══════════════════════════════════════════════
# DOCKER OPERATIONS
# ══════════════════════════════════════════════


def docker_ps() -> str:
    """List all containers with status."""
    result = run_shell(
        "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' --no-trunc",
        timeout=10,
    )
    return result["output"]


def docker_logs(container: str, tail: int = 50) -> dict:
    """Get container logs. Validates container exists first."""
    check = run_shell(f"docker ps -q --filter name=^{container}$", timeout=5)
    if not check["output"].strip():
        # Try partial match
        check2 = run_shell(f"docker ps -q --filter name={container}", timeout=5)
        if not check2["output"].strip():
            return {
                "ok": False,
                "output": "",
                "error": f"Container '{container}' not found or not running.",
            }
    return run_shell(f"docker logs --tail {tail} {container} 2>&1", timeout=15)


def docker_restart(container: str) -> dict:
    """Restart a container. Validates first."""
    check = run_shell(f"docker ps -q --filter name={container}", timeout=5)
    if not check["output"].strip():
        return {
            "ok": False,
            "output": "",
            "error": f"Container '{container}' not running.",
        }
    return run_shell(f"docker restart {container}", timeout=30)


def docker_stats(container: str = "") -> dict:
    """Get container resource stats."""
    target = container if container else ""
    return run_shell(
        f"docker stats --no-stream {target} --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}'",
        timeout=15,
    )


def docker_compose(action: str, project: str = "infra") -> dict:
    """Manage docker compose projects. Allowed actions: ps, logs, restart, up -d, pull, down."""
    allowed = {"ps", "logs", "restart", "up -d", "pull", "down", "config"}
    if action not in allowed:
        return {
            "ok": False,
            "output": "",
            "error": f"Action '{action}' not allowed. Use: {', '.join(sorted(allowed))}",
        }
    if action == "down":
        return {
            "ok": False,
            "output": "",
            "error": "⛔ 'docker compose down' blocked — use 'restart' instead.",
        }

    compose_file = COMPOSE_INFRA
    workdir = "/srv/work/repos/fleet-infra/rmi-infra"
    if project == "backend":
        compose_file = f"{COMPOSE_BACKEND_DIR}/docker-compose.yml"
        workdir = COMPOSE_BACKEND_DIR

    return run_shell(
        f"cd {shlex.quote(workdir)} && docker compose -f {shlex.quote(compose_file)} {action} 2>&1",
        timeout=60,
    )


# ══════════════════════════════════════════════
# SYSTEM MONITORING
# ══════════════════════════════════════════════


def system_status() -> dict:
    """Comprehensive system status."""
    hostname = run_shell("hostname", timeout=3)["output"].strip()
    uptime_out = run_shell("uptime -p", timeout=3)["output"].strip()
    df_out = run_shell("df -h / | tail -1", timeout=5)["output"]
    mem_out = run_shell("free -h | grep Mem:", timeout=3)["output"]
    load_out = run_shell("cat /proc/loadavg", timeout=3)["output"]
    cpu_out = run_shell("nproc", timeout=3)["output"].strip()
    docker_ps_out = run_shell("docker ps --format '{{.Names}}' 2>/dev/null", timeout=5)[
        "output"
    ]
    docker_total = run_shell(
        "docker ps -a --format '{{.Names}}' 2>/dev/null | wc -l", timeout=5
    )["output"].strip()

    # Parse df: "Filesystem Size Used Avail Use% Mounted on" — get last line
    disk_parts = df_out.split()
    disk = {
        "total": disk_parts[1] if len(disk_parts) > 1 else "?",
        "used": disk_parts[2] if len(disk_parts) > 2 else "?",
        "free": disk_parts[3] if len(disk_parts) > 3 else "?",
        "pct": disk_parts[4] if len(disk_parts) > 4 else "?",
    }

    # Parse free -h: "Mem:  total  used  free  shared  buff/cache  available"
    mem_parts = mem_out.split()
    memory = {
        "total": mem_parts[1] if len(mem_parts) > 1 else "?",
        "used": mem_parts[2] if len(mem_parts) > 2 else "?",
        "free": mem_parts[3] if len(mem_parts) > 3 else "?",
        "available": mem_parts[6] if len(mem_parts) > 6 else "?",
    }

    # Parse loadavg: "0.15 0.25 0.18 1/456 12345"
    load_parts = load_out.split()
    cpu = {
        "load_1m": float(load_parts[0]) if load_parts else 0,
        "load_5m": float(load_parts[1]) if len(load_parts) > 1 else 0,
        "load_15m": float(load_parts[2]) if len(load_parts) > 2 else 0,
        "cores": int(cpu_out) if cpu_out.isdigit() else 0,
    }

    containers = [c for c in docker_ps_out.split("\n") if c.strip()]
    running = len(containers)

    # Check critical services
    svc_status = {}
    for svc in [
        "rmi-backend",
        "rugmunchbot",
        "rmi-postgres",
        "rmi-redis",
        "rmi-clickhouse",
        "rmi-neo4j",
        "rmi-prometheus",
    ]:
        s = run_shell(
            f"systemctl is-active {svc} 2>/dev/null || docker ps --filter name={svc} --format '{{{{.Status}}}}' 2>/dev/null | head -1",
            timeout=5,
        )
        out = s["output"].strip()
        if out in ("active",) or "Up" in out:
            svc_status[svc] = "🟢 up"
        elif out:
            svc_status[svc] = f"🔴 {out[:20]}"
        else:
            svc_status[svc] = "⚫ unknown"

    return {
        "hostname": hostname,
        "uptime": uptime_out.replace("up ", ""),
        "disk": disk,
        "memory": memory,
        "cpu": cpu,
        "docker": {
            "running": running,
            "total": int(docker_total) if docker_total.isdigit() else 0,
            "containers": containers,
        },
        "services": svc_status,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def format_status(s: dict) -> str:
    """Format system_status() dict into Telegram markdown message."""
    disk_pct = s["disk"]["pct"].replace("%", "")
    disk_emoji = (
        "🟢" if float(disk_pct) < 70 else "🟡" if float(disk_pct) < 85 else "🔴"
    )
    mem_pct = (
        int(
            float(s["memory"]["used"].replace("Gi", "").replace("G", ""))
            / float(s["memory"]["total"].replace("Gi", "").replace("G", ""))
            * 100
        )
        if "G" in s["memory"]["total"]
        else 0
    )
    mem_emoji = "🟢" if mem_pct < 70 else "🟡" if mem_pct < 85 else "🔴"

    lines = [
        f"🖥️ *RMI Fleet Status* — {s['hostname']}",
        "",
        f"⏱ Uptime: `{s['uptime']}`",
        f"🕐 Time: `{s['time']}`",
        "",
        "*Resources:*",
        f"{disk_emoji} Disk: `{s['disk']['used']}/{s['disk']['total']}` ({s['disk']['pct']})",
        f"{mem_emoji} Memory: `{s['memory']['used']}/{s['memory']['total']}` (free: {s['memory']['available']})",
        f"💻 CPU: load `{s['cpu']['load_1m']}/{s['cpu']['load_5m']}/{s['cpu']['load_15m']}` ({s['cpu']['cores']} cores)",
        "",
        f"🐳 Docker: *{s['docker']['running']}* running / *{s['docker']['total']}* total",
        "",
        "*Critical Services:*",
    ]
    for svc, status in s["services"].items():
        lines.append(f"  {status} `{svc}`")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# SERVICE CONTROL (systemd)
# ══════════════════════════════════════════════


def service_control(name: str, action: str) -> dict:
    """Control a systemd or docker service."""
    allowed = {"status", "restart", "start", "stop"}
    if action not in allowed:
        return {"ok": False, "output": "", "error": f"Action '{action}' not allowed."}

    if name not in KNOWN_SERVICES:
        return {
            "ok": False,
            "output": "",
            "error": f"Unknown service '{name}'. Known: {', '.join(KNOWN_SERVICES)}",
        }

    # Try systemctl first, then docker
    svc_unit = f"{name}.service"
    result = run_shell(f"systemctl {action} {svc_unit} 2>&1", timeout=30)
    if (
        "not found" in result["output"].lower()
        or "not loaded" in result["output"].lower()
    ):
        # Fall back to docker
        result = run_shell(
            f"docker {action if action != 'status' else 'ps --filter name=' + name} {name} 2>&1",
            timeout=30,
        )
    return result


def service_list() -> str:
    """List known services with their current status."""
    lines = ["🔧 *Service Status*", ""]
    for svc in KNOWN_SERVICES:
        s = run_shell(f"systemctl is-active {svc}.service 2>/dev/null", timeout=3)
        status = s["output"].strip()
        if status == "active":
            emoji = "🟢"
        elif status == "inactive" or status == "dead":
            emoji = "🔴"
        elif status == "failed":
            emoji = "💀"
        else:
            # Check docker
            d = run_shell(
                f"docker ps --filter name={svc} --format '{{{{.Status}}}}' 2>/dev/null",
                timeout=3,
            )
            if "Up" in d["output"]:
                emoji, status = "🟢", "docker"
            else:
                emoji, status = "⚫", "unknown"
        lines.append(f"  {emoji} `{svc}` — {status}")
    return "\n".join(lines)


# ══════════════════════════════════════════════
# BACKUP CONTROL
# ══════════════════════════════════════════════


def trigger_backup(backup_type: str = "full") -> dict:
    """Trigger a backup. Types: db, full, status."""
    _log(f"backup:{backup_type}")

    if backup_type == "status":
        result = run_shell("ls -lt /backups/ 2>/dev/null | head -10", timeout=10)
        recent = run_shell(
            "find /backups/ -type f -mtime -1 2>/dev/null | wc -l", timeout=10
        )
        return {
            "ok": True,
            "output": f"📦 *Backup Status*\n\nRecent backups (last 24h): {recent['output'].strip()} files\n\nLatest files:\n```\n{result['output'][:2000]}\n```",
            "error": "",
            "duration": 0,
        }

    if backup_type == "db":
        script = "/srv/work/repos/fleet-infra/scripts/backup/db-backup.sh"
    else:  # full
        script = "/srv/work/repos/fleet-infra/scripts/backup/db-backup.sh"

    if not os.path.exists(script):
        return {
            "ok": False,
            "output": "",
            "error": f"Backup script not found: {script}",
        }

    result = run_shell(f"bash {shlex.quote(script)} 2>&1", timeout=120)
    return result


# ══════════════════════════════════════════════
# DEPLOYMENT
# ══════════════════════════════════════════════


def deploy_service(service: str) -> dict:
    """Deploy/rebuild a service."""
    _log(f"deploy:{service}")

    cmds = {
        "backend": (
            f"cd {shlex.quote(COMPOSE_BACKEND_DIR)} && "
            "docker compose build --no-cache rmi-backend 2>&1 && "
            "docker compose up -d rmi-backend 2>&1"
        ),
        "bot": (
            "cd /srv/work/repos/rugmunchbot/bot/app/telegram_bot && "
            "docker compose build 2>&1 && docker compose up -d 2>&1"
        ),
        "admin": (
            "cd /srv/work/repos/rugmunchbot/bot/app/telegram_bot && "
            "docker compose build 2>&1 && docker compose up -d 2>&1"
        ),
        "infra": (
            f"cd /srv/work/repos/fleet-infra/rmi-infra && docker compose -f {shlex.quote(COMPOSE_INFRA)} up -d 2>&1"
        ),
    }

    if service not in cmds:
        return {
            "ok": False,
            "output": "",
            "error": f"Unknown service '{service}'. Known: {', '.join(cmds.keys())}",
        }

    return run_shell(cmds[service], timeout=120)


# ══════════════════════════════════════════════
# HEALTH / ALERTS
# ══════════════════════════════════════════════


def health_check() -> dict:
    """Run quick health checks across the fleet."""
    checks = {}

    # Backend API
    b = run_shell(
        "curl -sf http://localhost:8000/health 2>&1 || echo 'DOWN'", timeout=5
    )
    checks["rmi-backend"] = (
        "🟢"
        if "healthy" in b["output"].lower() or "ok" in b["output"].lower()
        else "🔴"
    )

    # Database
    pg = run_shell(
        "docker exec rmi-postgres pg_isready -U rmi 2>&1 || echo 'DOWN'", timeout=5
    )
    checks["postgres"] = "🟢" if "accepting" in pg["output"].lower() else "🔴"

    # Redis
    rd = run_shell(
        "docker exec rmi-redis redis-cli ping 2>&1 || echo 'DOWN'", timeout=5
    )
    checks["redis"] = "🟢" if "PONG" in rd["output"] else "🔴"

    # ClickHouse
    ch = run_shell("curl -sf http://localhost:8123/ping 2>&1 || echo 'DOWN'", timeout=5)
    checks["clickhouse"] = "🟢" if "Ok" in ch["output"] else "🔴"

    # Qdrant
    qd = run_shell(
        "curl -sf http://localhost:6333/health 2>&1 || echo 'DOWN'", timeout=5
    )
    checks["qdrant"] = (
        "🟢" if "ok" in qd["output"].lower() or "200" in qd["output"] else "🔴"
    )

    # Neo4j
    n4 = run_shell("curl -sf http://localhost:7474 2>&1 || echo 'DOWN'", timeout=5)
    checks["neo4j"] = (
        "🟢" if "neo4j" in n4["output"].lower() or "200" in n4["output"] else "🔴"
    )

    # MinIO
    mn = run_shell(
        "curl -sf http://localhost:9000/minio/health/live 2>&1 || echo 'DOWN'",
        timeout=5,
    )
    checks["minio"] = (
        "🟢" if "200" in mn["output"] or "ok" in mn["output"].lower() else "🔴"
    )

    return checks


def format_health(checks: dict) -> str:
    lines = ["📊 *Fleet Health Check*", ""]
    all_up = True
    for name, status in checks.items():
        lines.append(f"  {status} `{name}`")
        if "🔴" in status:
            all_up = False
    lines.append("")
    lines.append("✅ All systems healthy" if all_up else "⚠️ Some systems degraded")
    return "\n".join(lines)
