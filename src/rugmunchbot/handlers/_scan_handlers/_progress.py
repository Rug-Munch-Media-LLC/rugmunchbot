#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (progress helpers (_edit_stage_if_slow/_staged_progress/_stop_task)).

Split out of ``handlers/_scan_handlers.py`` (degodded 2026-08-16). Re-exported
by ``handlers._scan_handlers`` and ``handlers.scanning`` so existing
importers/tests keep working unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Message

logger = get_logger("rmi_bot")


async def _edit_stage_if_slow(
    msg: Message,
    text: str,
    *,
    started: float,
    min_elapsed: float = 3.0,
) -> None:
    """Refresh the in-flight scan message to ``text`` once the scan has been
    running for at least ``min_elapsed`` seconds.

    Fast scans (cache hits, quick providers) return before the threshold and
    never see placeholder churn; slow scans read like a human analyst working
    through the checks. Fail-open: any Telegram hiccup is logged, not raised.
    """
    if time.monotonic() - started < min_elapsed:
        return
    try:
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("scan stage edit skipped", exc_info=True)


async def _staged_progress(
    msg: Message,
    stages: list[tuple[str, float]],
    *,
    started: float,
) -> None:
    """Drive ``msg`` through ``stages`` (``(text, min_elapsed_s)``) while a
    long scan runs in a sibling task.

    Each stage fires only once the scan has been running for at least
    ``min_elapsed_s`` seconds. The caller cancels this task the moment the
    scan result lands, so a fast scan is never clobbered by an interim edit.
    """
    for text, min_elapsed in stages:
        wait = started + min_elapsed - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            await msg.edit_text(text, parse_mode=ParseMode.HTML)
        except (TelegramError, ConnectionError, OSError, TimeoutError):
            logger.debug("scan staged progress edit skipped", exc_info=True)


async def _stop_task(task: asyncio.Task[Any] | None) -> None:
    """Cancel a background helper task and await its shutdown (no dangling
    coroutine / CancelledError noise). Safe to call with ``None``."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
