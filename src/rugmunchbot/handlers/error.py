#!/usr/bin/env python3
"""
RugMunchBot -- Global error handler.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING

from telegram import Update
from telegram.error import NetworkError, RetryAfter, TimedOut

from app.domains.telegram.rugmunchbot.support.logging import get_logger

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
        from app.domains.telegram.rugmunchbot.observability import record_error

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
        logger.warning("telegram_rate_limited retry_after=%.1fs sleeping=%.1fs", retry_after, backoff)
        await asyncio.sleep(backoff)
        return  # transient — no user-facing error

    if isinstance(error, (TimedOut, NetworkError)):
        # Transient transport failures (timeout / connection reset): log only.
        # Replying here floods users during API outages and slows the poll loop.
        logger.warning("telegram_transient_error type=%s", type(error).__name__)
        return

    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "\u26a0\ufe0f Something went wrong. Please try again.\nIf the issue persists, contact support.",
                parse_mode="HTML",
            )
