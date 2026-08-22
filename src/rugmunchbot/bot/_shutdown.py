"""Graceful shutdown — SIGTERM handling and headless-client teardown."""

from __future__ import annotations

import asyncio
import signal
from datetime import time as dtime  # noqa: F401 — used by daily_scam_tip if re-enabled
from typing import TYPE_CHECKING, Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


_shutdown_tasks: set[asyncio.Task[Any]] = set()


if TYPE_CHECKING:
    from telegram.ext import (
        Application,
    )


async def close_headless_on_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Close the singleton Telethon MTProto client (idempotent, bounded).

    Runs before Application.stop() waits on jobs, so in-flight Telethon sweeps
    fail fast instead of blocking shutdown indefinitely.
    """
    from rugmunchbot._vendor.headless import shutdown_headless_client

    try:
        await shutdown_headless_client()
    except Exception:
        logger.warning("headless_client_shutdown_error", exc_info=True)


async def _graceful_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Close clients first, then hand control back to PTB's normal stop path."""
    try:
        await close_headless_on_shutdown(app)
    finally:
        app.stop_running()


def install_sigterm_shutdown(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Replace PTB's default stop-signal handlers with shutdown-aware ones.

    Must run on the app's event loop (post_init). add_signal_handler replaces
    the handler PTB registered in run_polling() for the same signal.
    """
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("shutdown_signal_received — closing clients, stopping app")
        # Keep a strong reference so the task is never GC'd mid-shutdown.
        _shutdown_tasks.add(loop.create_task(_graceful_shutdown(app)))

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            logger.debug("signal_handler_not_available sig=%s", sig, exc_info=True)
