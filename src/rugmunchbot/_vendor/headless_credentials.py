# VENDORED 2026-08-24 from rmi-backend/app/domains/telegram/headless_credentials.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Headless Telegram Client — Credential Management
=================================================
Fetches Telegram API credentials from environment variables.

Values are supplied by the deployment via env vars (``RMI_TG_API_ID`` /
``RMI_TG_API_HASH`` / ``RMI_TG_PHONE``); they are never hardcoded or
committed. No secret value is ever logged or written to the repo.
"""

from __future__ import annotations

import os

# Env vars that provide the Telegram API credentials. The deployment populates
# them (e.g. from a secrets manager) — the source of truth is never committed.
ENV_API_ID = "RMI_TG_API_ID"
ENV_API_HASH = "RMI_TG_API_HASH"
ENV_PHONE = "RMI_TG_PHONE"


def get_api_credentials() -> tuple[str, int, str]:
    """Fetch Telegram API credentials from the environment.

    Returns:
        Tuple of (phone_number, api_id, api_hash).

    Raises:
        RuntimeError: If credentials cannot be fetched. The message names the
            env vars that must be set (never their values).
    """
    api_id_raw = os.getenv(ENV_API_ID, "")
    api_hash = os.getenv(ENV_API_HASH, "")
    phone = os.getenv(ENV_PHONE, "")

    missing: list[str] = []
    if not api_id_raw:
        missing.append(ENV_API_ID)
    if not api_hash:
        missing.append(ENV_API_HASH)
    if not phone:
        missing.append(ENV_PHONE)
    if missing:
        raise RuntimeError(
            f"Cannot fetch Telegram API credentials: set {', '.join(missing)} (values are provided by the deployment)."
        )

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError(f"Telegram api_id from {ENV_API_ID} is not an integer") from exc

    return (phone, api_id, api_hash)


def get_session_path() -> str:
    """Get path for Telethon session file."""
    return os.path.join(
        os.path.dirname(__file__),
        "data",
        "headless_session",
    )
