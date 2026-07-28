#!/usr/bin/env python3
"""
RMI Admin Terminal — Shell execution with session management
=============================================================
Terminal mode: every message = shell command.
Features: working directory state, command history, safety filter,
output streaming, session isolation per user.
"""

import os
import shlex
import subprocess
import time
from datetime import UTC, datetime

# ══════════════════════════════════════════════
# PER-USER SESSIONS
# ══════════════════════════════════════════════


class TerminalSession:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.cwd = "/root"
        self.history: list[str] = []
        self.created = datetime.now(UTC)


# In-memory session store (resets on bot restart — acceptable for admin tool)
_sessions: dict[int, TerminalSession] = {}


def enter_terminal(user_id: int) -> TerminalSession:
    """Create or return existing terminal session for user."""
    if user_id not in _sessions:
        _sessions[user_id] = TerminalSession(user_id)
    return _sessions[user_id]


def exit_terminal(user_id: int):
    """Destroy terminal session."""
    _sessions.pop(user_id, None)


def in_terminal(user_id: int) -> bool:
    return user_id in _sessions


# ══════════════════════════════════════════════
# COMMAND SAFETY
# ══════════════════════════════════════════════

BLOCKED = [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf .",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "init 0",
    "init 6",
    "mkfs",
    "mkswap",
    "dd if=/dev/zero of=/dev/",
    "dd if=/dev/urandom of=/dev/",
    ":(){ :|:& };:",  # fork bomb
    "> /dev/sda",
    "> /dev/vda",
    "> /dev/nvme",
    "chmod 777 /",
    "chmod -R 777 /",
    "chown -R",
    "docker rm -f $(docker ps -aq)",
    "docker system prune -af --volumes",
    "fdisk",
    "parted",
    "wipefs",
]

WARNING_PATTERNS = [
    ("rm -rf", "⚠️ Recursive delete detected"),
    ("docker rm", "⚠️ Container removal detected"),
    ("docker system prune", "⚠️ Docker cleanup detected — may remove data"),
]


def check_command(cmd: str) -> tuple[bool, str]:
    """Returns (is_safe, message)."""
    cmd_clean = cmd.lower().replace(" ", "")
    for blocked in BLOCKED:
        if blocked.lower().replace(" ", "") in cmd_clean:
            return False, "⛔ BLOCKED: Command matches dangerous pattern"
    for pattern, warning in WARNING_PATTERNS:
        if pattern.lower().replace(" ", "") in cmd_clean:
            return True, warning  # Allow but warn
    return True, ""


# ══════════════════════════════════════════════
# COMMAND EXECUTION
# ══════════════════════════════════════════════


def execute(session: TerminalSession, cmd: str, timeout: int = 30) -> dict:
    """Execute a command in the user's session context.
    Returns {ok, output, cwd, duration, warning}"""

    # Handle built-in commands
    if cmd.strip() in ("exit", "quit", "/exit"):
        return {
            "ok": True,
            "output": "Exiting terminal mode.",
            "cwd": session.cwd,
            "duration": 0,
            "warning": "",
            "exit": True,
        }

    if cmd.strip().startswith("cd "):
        new_dir = cmd.strip()[3:].strip().strip('"').strip("'")
        if not new_dir:
            new_dir = "/root"
        try:
            # Resolve relative paths
            if not new_dir.startswith("/"):
                new_dir = os.path.join(session.cwd, new_dir)
            new_dir = os.path.normpath(new_dir)
            if os.path.isdir(new_dir):
                session.cwd = new_dir
                return {
                    "ok": True,
                    "output": f"📁 {new_dir}",
                    "cwd": session.cwd,
                    "duration": 0,
                    "warning": "",
                    "exit": False,
                }
            else:
                return {
                    "ok": False,
                    "output": f"❌ Not a directory: {new_dir}",
                    "cwd": session.cwd,
                    "duration": 0,
                    "warning": "",
                    "exit": False,
                }
        except Exception as e:
            return {
                "ok": False,
                "output": f"❌ {e}",
                "cwd": session.cwd,
                "duration": 0,
                "warning": "",
                "exit": False,
            }

    if cmd.strip() in ("pwd",):
        return {
            "ok": True,
            "output": session.cwd,
            "cwd": session.cwd,
            "duration": 0,
            "warning": "",
            "exit": False,
        }

    # Safety check
    safe, msg = check_command(cmd)
    if not safe:
        return {
            "ok": False,
            "output": msg,
            "cwd": session.cwd,
            "duration": 0,
            "warning": "",
            "exit": False,
        }

    # Execute — use shell=False with shlex.split() to prevent shell injection
    start = time.time()
    session.history.append(cmd)
    if len(session.history) > 100:
        session.history = session.history[-100:]

    try:
        args = shlex.split(cmd)
        result = subprocess.run(
            args,
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            cwd=session.cwd,
        )
        output = result.stdout.strip()
        if result.stderr.strip():
            if output:
                output += "\n" + result.stderr.strip()
            else:
                output = result.stderr.strip()

        if not output:
            output = "(no output)"

        # Truncate for Telegram
        if len(output) > 3500:
            output = output[:3400] + "\n\n[... truncated at 3500 chars]"

        return {
            "ok": result.returncode == 0,
            "output": output,
            "cwd": session.cwd,
            "duration": time.time() - start,
            "warning": msg if msg else "",
            "exit": False,
        }
    except ValueError as e:
        # shlex.split failed (unterminated quotes)
        return {
            "ok": False,
            "output": f"❌ Invalid command syntax: {e!s}",
            "cwd": session.cwd,
            "duration": time.time() - start,
            "warning": "",
            "exit": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": f"⏰ Timed out after {timeout}s",
            "cwd": session.cwd,
            "duration": timeout,
            "warning": "",
            "exit": False,
        }
    except Exception as e:
        return {
            "ok": False,
            "output": f"❌ Error: {e!s}",
            "cwd": session.cwd,
            "duration": time.time() - start,
            "warning": "",
            "exit": False,
        }


# ══════════════════════════════════════════════
# FORMATTING
# ══════════════════════════════════════════════


def format_prompt(session: TerminalSession) -> str:
    """Format a shell-like prompt string."""
    hostname = os.uname().nodename.split(".")[0]
    cwd_display = (
        session.cwd.replace("/root", "~")
        if session.cwd.startswith("/root")
        else session.cwd
    )
    return f"`{hostname}:{cwd_display}$`"


def format_result(result: dict, session: TerminalSession) -> str:
    """Format execution result for Telegram display."""
    if result.get("exit"):
        return "👋 Exited terminal mode."

    duration = f" ({result['duration']:.1f}s)" if result["duration"] > 1 else ""

    lines = [format_prompt(session) + f" {duration}"]

    if result.get("warning"):
        lines.append(result["warning"])

    status = "✅" if result["ok"] else "❌"
    lines.append(f"{status}\n```\n{result['output']}\n```")

    return "\n".join(lines)


# ══════════════════════════════════════════════
# BATCH / QUICK COMMANDS
# ══════════════════════════════════════════════

QUICK_COMMANDS = {
    "docker-ps": "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'",
    "disk": "df -h / && echo '---' && du -sh /srv/* 2>/dev/null | sort -rh | head -10",
    "memory": "free -h && echo '---' && ps aux --sort=-%mem | head -6",
    "cpu": "uptime && echo '---' && top -bn1 | head -5",
    "network": "ss -tlnp 2>/dev/null | head -20",
    "processes": "ps aux --sort=-%cpu | head -15",
    "services": "systemctl list-units --type=service --state=running | grep rmi",
    "backups": "ls -lth /backups/ 2>/dev/null | head -10",
    "logs-backend": "journalctl -u rmi-backend.service --no-pager -n 30 2>/dev/null || docker logs rmi-backend --tail 30 2>&1",
    "logs-nginx": "journalctl -u nginx --no-pager -n 20 2>/dev/null || tail -20 /var/log/nginx/access.log 2>/dev/null",
}


def run_quick(name: str) -> dict:
    """Run a predefined quick command."""
    if name in QUICK_COMMANDS:
        session = TerminalSession(0)  # System session
        return execute(session, QUICK_COMMANDS[name])
    return {
        "ok": False,
        "output": f"Unknown quick command: {name}",
        "cwd": "/",
        "duration": 0,
        "warning": "",
        "exit": False,
    }


# Quick command buttons for the terminal menu
def quick_commands_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = [
        [
            InlineKeyboardButton("💾 Disk", callback_data="term_quick_disk"),
            InlineKeyboardButton("🧠 Memory", callback_data="term_quick_memory"),
            InlineKeyboardButton("⚡ CPU", callback_data="term_quick_cpu"),
        ],
        [
            InlineKeyboardButton("🐳 Docker PS", callback_data="term_quick_docker-ps"),
            InlineKeyboardButton("🔧 Services", callback_data="term_quick_services"),
        ],
        [
            InlineKeyboardButton(
                "📋 Backend Logs", callback_data="term_quick_logs-backend"
            ),
            InlineKeyboardButton("🌐 Network", callback_data="term_quick_network"),
        ],
        [InlineKeyboardButton("📦 Backups", callback_data="term_quick_backups")],
    ]
    return InlineKeyboardMarkup(buttons)
