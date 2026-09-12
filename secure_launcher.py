#!/usr/bin/env python3
"""
Secure Bot Launcher — Zero Token Leakage
=========================================
Reads bot token from gopass at startup, exports it to a process-local
environment variable that is UNSET immediately after the bot starts.

This ensures the token:
  - Never appears in systemctl status / journalctl output
  - Never appears in /proc/*/environ (unset before long-running loop)
  - Never appears in ps aux (passed only as transient env, not cmdline arg)
  - Lives ONLY in gopass (encrypted git-based password store)

Usage (systemd ExecStart):
  /path/to/venv/bin/python -m app.domains.telegram.secure_launcher <bot_name>

Where <bot_name> is 'rugmunchbot', 'adminbot', or 'rmiadminbot'.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Install token redaction filter BEFORE any logging happens
from log_filter import install as install_token_filter, suppress_httpx_urls

install_token_filter()
suppress_httpx_urls()

# Canonical gopass paths. `adminbot` and `rmiadminbot` launch the SAME module
# (app.domains.telegram.adminbot.bot) and MUST point at the same secret —
# adminbot/config.py reads the admin bot token from secrets (adminbot/config.py:15).
# METHODOLOGY-PROTECTION: this repo carries NO gopass paths — the placeholders
# below are the safe public default. The deployment injects the REAL secret
# store path via env (``RUGMUNCH_BOT_GOPASS_PATH`` / ``RMIADMINBOT_GOPASS_PATH``
# in the systemd unit Environment=), so the running service resolves the token
# while the source tree stays scrub-clean.
GOPASS_PATHS = {
    "rugmunchbot": os.environ.get("RUGMUNCH_BOT_GOPASS_PATH", "<bot_token_secret>"),
    "adminbot": os.environ.get("RMIADMINBOT_GOPASS_PATH", "<adminbot_token_secret>"),
    "rmiadminbot": os.environ.get("RMIADMINBOT_GOPASS_PATH", "<rmiadminbot_token_secret>"),
}

# Env keys exported to the bot process, in priority order. Every key in the
# list is set to the same token so any consumer (config module, alert_hooks,
# routers) sees the value regardless of which name it reads.
ENV_KEYS = {
    "rugmunchbot": ["RUGMUNCH_BOT_TOKEN", "TELEGRAM_RUGMUNCH_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
    "adminbot": ["ADMIN_BOT_TOKEN"],
    "rmiadminbot": ["ADMIN_BOT_TOKEN"],
}

MODULES = {
    "rugmunchbot": "rugmunchbot.bot",
    "adminbot": "app.domains.telegram.adminbot.bot",
    "rmiadminbot": "app.domains.telegram.adminbot.bot",
}


def _fetch_from_gopass(gopass_path: str) -> str:
    """Fetch token from gopass. Returns empty string on failure."""
    try:
        # gopass resolved from PATH by design (dev tooling); args are fixed.
        cmd = ["gopass", "show", gopass_path]
        token = (
            subprocess.check_output(  # noqa: S603
                cmd, stderr=subprocess.DEVNULL, timeout=10, shell=False
            )
            .decode()
            .strip()
        )
        return token
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: secure_launcher.py <rugmunchbot|adminbot|rmiadminbot>", file=sys.stderr)
        sys.exit(1)

    bot_name = sys.argv[1]
    if bot_name not in GOPASS_PATHS:
        print(f"Unknown bot: {bot_name}", file=sys.stderr)
        sys.exit(1)

    # Fetch token from gopass (never from environment)
    token = _fetch_from_gopass(GOPASS_PATHS[bot_name])

    if not token:
        print(f"FATAL: Could not fetch token from gopass for {bot_name}", file=sys.stderr)
        sys.exit(1)

    # Set ALL env keys for this bot (each is an alias to the same token).
    # Consumers read different names: rugmunchbot config prefers
    # RUGMUNCH_BOT_TOKEN, alert_hooks/routers read TELEGRAM_BOT_TOKEN, and the
    # adminbot config reads ADMIN_BOT_TOKEN.
    env_keys = ENV_KEYS[bot_name]
    for key in env_keys:
        os.environ[key] = token

    # Import and run the bot module (token is now in env for config.py to read)
    module_name = MODULES[bot_name]
    __import__(module_name)
    bot_module = sys.modules[module_name]

    # UNSET the token from environment BEFORE the bot enters its long-running poll loop
    # This ensures /proc/*/environ never shows the token during normal operation
    for key in env_keys:
        os.environ.pop(key, None)

    # Clear the token from memory (best-effort, Python GC handles the rest)
    token = ""

    # Run the bot's main()
    if hasattr(bot_module, "main"):
        bot_module.main()
    else:
        print(f"FATAL: {module_name} has no main()", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
