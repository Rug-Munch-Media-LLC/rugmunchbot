#!/usr/bin/env python3
"""
RMI Admin Monitor — Fleet monitoring & health checks
======================================================
Live system dashboard, Prometheus/Grafana integration,
service health matrix, resource trend alerts.
"""

import subprocess
from datetime import datetime

import httpx

# ══════════════════════════════════════════════
# LIVE FLEET SNAPSHOT
# ══════════════════════════════════════════════


def fleet_snapshot() -> dict:
    """Take a complete fleet snapshot. Returns structured data for display."""

    def run(cmd, timeout=5):
        try:
            r = subprocess.run(
                cmd, shell=True, timeout=timeout, capture_output=True, text=True
            )
            return r.stdout.strip() or r.stderr.strip()
        except Exception:
            return "ERROR"

    # ── System ──
    hostname = run("hostname")
    uptime = run("uptime -p").replace("up ", "")
    load = run("cat /proc/loadavg")
    load_parts = load.split()

    # ── Disk ──
    df = run("df -h / | tail -1")
    df_parts = df.split()

    # ── Memory ──
    mem = run("free -h | grep Mem:")
    mem_parts = mem.split()

    # ── Docker ──
    docker_ps = run("docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null")
    containers = []
    for line in docker_ps.split("\n"):
        if line.strip():
            parts = line.split(" ", 1)
            name = parts[0]
            status = parts[1] if len(parts) > 1 else "?"
            healthy = (
                "🟢"
                if ("Up" in status and "unhealthy" not in status.lower())
                else "🔴"
                if "Up" in status
                else "⚫"
            )
            containers.append({"name": name, "status": status[:40], "healthy": healthy})

    docker_total = len([c for c in containers if "Up" in c["status"]])

    # ── Critical service checks ──
    services = {}

    # Backend health
    try:
        r = httpx.get("http://localhost:8000/health", timeout=3)
        services["rmi-backend"] = "🟢" if r.status_code == 200 else "🔴"
    except Exception:
        services["rmi-backend"] = "🔴"

    # Postgres
    pg = run("docker exec rmi-postgres pg_isready -U rmi 2>/dev/null || echo 'DOWN'")
    services["rmi-postgres"] = "🟢" if "accepting" in pg.lower() else "🔴"

    # Redis/Dragonfly
    rd = run(
        "docker exec rmi-redis redis-cli -a $REDIS_PASSWORD ping 2>/dev/null || echo 'DOWN'"
    )
    # Fallback: just check if container is up
    if "PONG" not in rd:
        rd_up = run("docker ps --filter name=rmi-redis --format '{{.Status}}'")
        services["rmi-redis"] = "🟢" if "Up" in rd_up else "🔴"
    else:
        services["rmi-redis"] = "🟢"

    # ClickHouse
    try:
        r = httpx.get("http://localhost:8123/ping", timeout=3)
        services["rmi-clickhouse"] = "🟢" if "Ok" in r.text else "🔴"
    except Exception:
        services["rmi-clickhouse"] = "🔴"

    # Neo4j
    try:
        r = httpx.get("http://localhost:7474", timeout=3)
        services["rmi-neo4j"] = "🟢" if r.status_code == 200 else "🔴"
    except Exception:
        services["rmi-neo4j"] = "🔴"

    # Qdrant
    try:
        r = httpx.get("http://localhost:6333/health", timeout=3)
        services["rmi-qdrant"] = "🟢" if r.status_code == 200 else "🔴"
    except Exception:
        services["rmi-qdrant"] = "🔴"

    # MinIO
    try:
        r = httpx.get("http://localhost:9000/minio/health/live", timeout=3)
        services["rmi-minio"] = "🟢" if r.status_code == 200 else "🔴"
    except Exception:
        services["rmi-minio"] = "🔴"

    # Prometheus
    try:
        r = httpx.get("http://localhost:9090/-/healthy", timeout=3)
        services["fleet-prometheus"] = "🟢" if r.status_code == 200 else "🔴"
    except Exception:
        services["fleet-prometheus"] = "🔴"

    # Grafana
    try:
        r = httpx.get("http://localhost:3003/api/health", timeout=3)
        services["fleet-grafana"] = "🟢" if r.status_code == 200 else "🔴"
    except Exception:
        services["fleet-grafana"] = "🔴"

    return {
        "hostname": hostname,
        "uptime": uptime,
        "load_1m": float(load_parts[0]) if load_parts else 0,
        "load_5m": float(load_parts[1]) if len(load_parts) > 1 else 0,
        "load_15m": float(load_parts[2]) if len(load_parts) > 2 else 0,
        "disk_total": df_parts[1] if len(df_parts) > 1 else "?",
        "disk_used": df_parts[2] if len(df_parts) > 2 else "?",
        "disk_free": df_parts[3] if len(df_parts) > 3 else "?",
        "disk_pct": df_parts[4] if len(df_parts) > 4 else "?",
        "mem_total": mem_parts[1] if len(mem_parts) > 1 else "?",
        "mem_used": mem_parts[2] if len(mem_parts) > 2 else "?",
        "mem_free": mem_parts[3] if len(mem_parts) > 3 else "?",
        "mem_avail": mem_parts[6] if len(mem_parts) > 6 else "?",
        "docker_running": docker_total,
        "docker_total": len(containers),
        "containers": containers,
        "services": services,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# ══════════════════════════════════════════════
# FORMAT FOR TELEGRAM
# ══════════════════════════════════════════════


def format_snapshot(snap: dict) -> str:
    """Format fleet snapshot into a beautiful Telegram Markdown message."""

    # Disk gauge
    disk_pct = snap["disk_pct"].replace("%", "")
    try:
        dp = float(disk_pct)
        disk_bar = "█" * int(dp / 5) + "░" * (20 - int(dp / 5))
        disk_emoji = "🟢" if dp < 70 else "🟡" if dp < 85 else "🔴"
    except ValueError:
        disk_bar = "?"
        disk_emoji = "⚫"

    # Memory gauge
    try:
        mem_total = (
            snap["mem_total"].replace("Gi", "").replace("G", "").replace("Mi", "")
        )
        mem_used = snap["mem_used"].replace("Gi", "").replace("G", "").replace("Mi", "")
        mp = (float(mem_used) / float(mem_total)) * 100
        mem_bar = "█" * int(mp / 5) + "░" * (20 - int(mp / 5))
        mem_emoji = "🟢" if mp < 70 else "🟡" if mp < 85 else "🔴"
    except (ValueError, ZeroDivisionError):
        mem_bar = "?" * 20
        mem_emoji = "⚫"

    # Service grid
    service_lines = []
    svc_items = list(snap["services"].items())
    for i in range(0, len(svc_items), 3):
        row = svc_items[i : i + 3]
        line = "  ".join(f"{s} `{n}`" for n, s in row)
        service_lines.append(line)

    # Docker summary
    container_lines = []
    for c in snap["containers"][:15]:
        container_lines.append(f"  {c['healthy']} `{c['name']}` — {c['status']}")
    if len(snap["containers"]) > 15:
        container_lines.append(f"  ... and {len(snap['containers']) - 15} more")

    lines = [
        f"🖥️ *RMI Fleet Dashboard* — `{snap['hostname']}`",
        f"⏱ Uptime: {snap['uptime']}  |  {snap['time']}",
        "",
        "*Resources*",
        f"{disk_emoji} Disk: `{disk_bar}` {snap['disk_pct']}  ({snap['disk_used']}/{snap['disk_total']})",
        f"{mem_emoji} RAM:  `{mem_bar}` {mem_used}/{mem_total}",
        f"💻 CPU Load: `{snap['load_1m']:.1f}` / `{snap['load_5m']:.1f}` / `{snap['load_15m']:.1f}`",
        "",
        "*Services*",
        *service_lines,
        "",
        f"🐳 *Docker* ({snap['docker_running']}/{snap['docker_total']} running)",
        *container_lines,
    ]

    return "\n".join(lines)


# ══════════════════════════════════════════════
# PROMETHEUS QUERIES
# ══════════════════════════════════════════════


def query_prometheus(query: str) -> dict:
    """Query Prometheus API. Returns {ok, data, error}."""
    try:
        r = httpx.get(f"http://localhost:9090/api/v1/query?query={query}", timeout=5)
        if r.status_code == 200:
            return {"ok": True, "data": r.json(), "error": ""}
        return {
            "ok": False,
            "data": None,
            "error": f"Prometheus returned {r.status_code}",
        }
    except Exception as e:
        return {"ok": False, "data": None, "error": str(e)}


def get_alerts() -> str:
    """Get active Prometheus alerts."""
    result = query_prometheus("ALERTS{alertstate='firing'}")
    if not result["ok"]:
        return "⚠️ Cannot reach Prometheus"

    data = result["data"]
    alerts = []
    for r in data.get("data", {}).get("result", []):
        name = r["metric"].get("alertname", "unknown")
        severity = r["metric"].get("severity", "unknown")
        alerts.append(
            f"  {'🔴' if severity == 'critical' else '🟡'} {name} ({severity})"
        )

    if not alerts:
        return "✅ No active alerts"

    return "🚨 *Active Alerts*\n" + "\n".join(alerts)


def get_disk_trend() -> str:
    """Get disk usage trend over last hour."""
    result = query_prometheus("node_filesystem_avail_bytes{mountpoint='/'}")
    if result["ok"]:
        data = result["data"]
        for r in data.get("data", {}).get("result", []):
            free_bytes = int(float(r["value"][1]))
            free_gb = free_bytes / (1024**3)
            return f"💾 Free disk: {free_gb:.1f} GB"
    return "⚠️ Cannot read disk metrics"


def alert_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="alerts"),
                InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
            ],
            [
                InlineKeyboardButton("📊 Grafana", url="http://100.104.130.92:3003"),
                InlineKeyboardButton("📈 Prometheus", url="http://100.104.130.92:9090"),
            ],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
        ]
    )
