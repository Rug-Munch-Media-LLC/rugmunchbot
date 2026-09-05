#!/usr/bin/env python3
"""
RugMunchBot -- Global error handler.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
from telegram import Update
from telegram.error import NetworkError, RetryAfter, TimedOut

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

# Cap the rate-limit backoff we sleep in the error handler so a long flood-wait
# can never stall the event loop or block Application.stop() past TimeoutStopSec.
RETRY_AFTER_SLEEP_CAP = 5.0


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Global PTB error handler: log + backoff on 429, stay quiet on transport errors."""
    error = ctx.error
    logger.error(f"Exception: {error}", exc_info=error)

    # Metrics: count every error (incl. transient) + remember the last one.
    try:
        from rugmunchbot.observability import record_error

        record_error(kind=type(error).__name__, message=str(error))
    except Exception:
        logger.debug("observability record_error failed", exc_info=True)

    if isinstance(error, RetryAfter):
        # Telegram 429 (flood control): respect the server-provided backoff so
        # PTB's retry-aware paths recover instead of hammering the API. Bounded
        # so a long flood-wait cannot block shutdown.
        retry_after = getattr(error, "retry_after", 1.0)
        if isinstance(retry_after, timedelta):
            retry_after = retry_after.total_seconds()
        backoff = min(float(retry_after or 1.0), RETRY_AFTER_SLEEP_CAP)
        logger.warning(
            "telegram_rate_limited retry_after=%.1fs sleeping=%.1fs", retry_after, backoff
        )
        await asyncio.sleep(backoff)
        return  # transient — no user-facing error

    if isinstance(error, (TimedOut, NetworkError)):
        # Transient transport failures (timeout / connection reset): log only.
        # Replying here floods users during API outages and slows the poll loop.
        logger.warning("telegram_transient_error type=%s", type(error).__name__)
        return

    if isinstance(update, Update) and update.effective_message:
        user_msg, error_id = _classify_error(error)
        if error_id:
            logger.error("user_facing_error id=%s type=%s", error_id, type(error).__name__)
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(user_msg, parse_mode="HTML")


def _error_id(error: Exception) -> str:
    """Short stable ID so a user-reported error maps to one journal line."""
    return hashlib.sha256(f"{type(error).__name__}:{error}".encode()).hexdigest()[:8]


def _classify_error(error: Exception) -> tuple[str, str | None]:
    """Map an exception to a short, specific user message.

    Returns ``(message, error_id)`` -- ``error_id`` is set only for the
    unknown bucket (so users can quote it and we can grep the journal).
    Messages stay short and dry: no apologies, no filler.
    """
    # Backend API failures carry an HTTP status -- classify on it.
    try:
        from rugmunchbot.tools._client import BackendApiError

        if isinstance(error, BackendApiError):
            status = getattr(error, "status_code", 0)
            if status == 429:
                return ("Rate limited upstream -- try again in a minute.", None)
            if status in (401, 402, 403):
                return ("That data needs a higher tier -- see /pricing.", None)
            if status == 404:
                return ("Not found -- double-check the address or handle.", None)
            if status >= 500 or status == 0:
                return ("Backend hiccup -- try again shortly.", None)
    except ImportError as e:
        logger.debug("error_classify:backend_api_error_import_failed err=%s", e)

    # Timeouts (asyncio, httpx, bare OSError timeouts).
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
        return ("Scan timed out -- try /scan again.", None)

    # Provider rate limits that surface as HTTP errors.
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
        return ("Rate limited -- retry in a minute.", None)

    # Unknown: generic message + error ID for log correlation.
    return (
        f"Something went wrong (error {_error_id(error)}). Try again -- if it "
        "repeats, send the error ID to support.",
        _error_id(error),
    )
