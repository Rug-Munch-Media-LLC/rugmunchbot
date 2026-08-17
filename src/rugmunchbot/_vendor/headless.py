# VENDORED 2026-08-17 from rmi-backend/app/domains/telegram/headless.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Headless Telegram Client
========================
Uses Telethon (MTProto) to manage Telegram groups programmatically
without requiring a browser or interactive login.

This is the public facade. Implementation is split across:
  - ``headless_models.py``  — GroupType, GroupConfig, GroupInfo
  - ``headless_credentials.py`` — Credential fetching from gopass
  - ``headless_client.py``  — HeadlessTelegramClient class
"""

from __future__ import annotations

import asyncio

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.headless_client import HeadlessTelegramClient
from rugmunchbot._vendor.headless_models import GroupConfig, GroupInfo, GroupType

logger = get_logger("telegram.headless")

__all__ = [
    "GroupConfig",
    "GroupInfo",
    "GroupType",
    "HeadlessTelegramClient",
    "_client_instance",
    "get_headless_client",
    "shutdown_headless_client",
]


# ── Singleton ───────────────────────────────────────────────────────────

_client_instance: HeadlessTelegramClient | None = None


async def get_headless_client() -> HeadlessTelegramClient:
    """Get or create the singleton headless Telegram client.

    The client is lazily initialized on first use and reused
    across the application lifecycle.
    """
    global _client_instance

    if _client_instance is None:
        _client_instance = HeadlessTelegramClient()
        await _client_instance.start()

    if not _client_instance.is_connected:
        await _client_instance.start()

    return _client_instance


async def shutdown_headless_client() -> None:
    """Gracefully shut down the headless client (idempotent, bounded).

    Should be called during application shutdown. Safe to call multiple times
    and from either the FastAPI lifespan or the bot process. The singleton is
    reset before stopping so concurrent callers never double-stop the client.
    """
    global _client_instance

    client = _client_instance
    _client_instance = None

    if client is None:
        return

    try:
        await asyncio.wait_for(client.stop(), timeout=7)
        logger.info("Headless client shut down")
    except TimeoutError:
        logger.warning("Headless client shutdown timed out")
    except Exception:
        logger.exception("Headless client shutdown failed")
