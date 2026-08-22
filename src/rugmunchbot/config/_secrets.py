#!/usr/bin/env python3
"""Secret resolution — gopass → env fallback, always lazy (no I/O at import).

Importing this package never spawns a gopass subprocess, reads no secret file,
and reads no env var. Resolvers here are invoked on demand via the lazy
machinery in the package ``__init__`` (PEP 562).
"""

from __future__ import annotations

import os
import subprocess

# ══════════════════════════════════════════════
# BOT TOKEN (gopass → env fallback)
# ══════════════════════════════════════════════


def _get_token(gopass_path: str, env_keys: list[str]) -> str:
    """Fetch token from gopass at runtime, falling back to env vars."""
    try:
        token = (
            subprocess.check_output(
                ["gopass", "show", gopass_path], stderr=subprocess.DEVNULL, timeout=5, shell=False
            )
            .decode()
            .strip()
        )
        if token:
            return token
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        from rugmunchbot.support.logging import get_logger

        get_logger("rmi.config").debug(
            "gopass token fetch failed, falling back to env: %s", e.__class__.__name__
        )
    for key in env_keys:
        env_token = os.getenv(key)
        if env_token:
            return env_token
    return ""


def _resolve_bot_token() -> str:
    """Resolve the bot token (gopass → env) lazily on first access.

    Deferred via the package ``__getattr__`` so that importing config never
    spawns a gopass subprocess. ``BOT_TOKEN`` is NOT bound at import —
    ``from ...config import BOT_TOKEN`` triggers this on demand and the value
    is memoized into the package namespace.
    """
    return _get_token(
        "rmi/rugmunchbot/bot_token",
        ["RUGMUNCH_BOT_TOKEN", "TELEGRAM_RUGMUNCH_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"],
    )


def _read_owner_ids() -> set[int]:
    """Owner/operator ids primitive — env ``TELEGRAM_ALLOWED_USERS`` read per call.

    UNCACHED primitive: the ``@lru_cache`` lives on the ``_resolve_owner_ids``
    wrapper in the package ``__init__`` so that a package reload resets the
    cache exactly like the old single-module config did.
    """
    raw = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
    if not raw:
        from rugmunchbot.support.logging import get_logger

        get_logger("rmi.config").warning(
            "TELEGRAM_ALLOWED_USERS is unset/empty — no owner/operator ids configured (owner commands disabled)."
        )
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}


# ══════════════════════════════════════════════
# BACKEND AUTH (X-API-Key)
# ══════════════════════════════════════════════
# The backend AuthMiddleware requires `X-API-Key` == RMI_AUTH_TOKEN on every
# non-public route (app/core/auth.py). The bot runs on the host (not in the
# backend container), so it reads the same secret file the backend container
# mounts as its Docker secret, falling back to the RMI_AUTH_TOKEN env var and
# then gopass. Sending the header ALSO bypasses the backend CSRF middleware
# (middleware_setup.py skips the check when X-API-Key is present).
_RMI_AUTH_SECRET_FILES = (
    "/srv/secrets/rmi_auth_token",  # Docker secret source — matches backend
    "/run/secrets/rmi_auth_token",  # in-container fallback (bot in Docker)
)


def _get_api_key() -> str:
    """Resolve the backend X-API-Key: secret file → env var → gopass.

    UNCACHED primitive: process-wide memoization lives on the
    ``_resolve_api_key`` wrapper in the package ``__init__`` (``@lru_cache``),
    so a package reload resets it exactly like the old single-module config.
    """
    for secret_file in _RMI_AUTH_SECRET_FILES:
        try:
            with open(secret_file) as f:
                key = f.read().strip()
            if key:
                return key
        except (FileNotFoundError, OSError):
            continue
    return _get_token("rmi/backend/rmi_auth_token", ["RMI_AUTH_TOKEN"])
