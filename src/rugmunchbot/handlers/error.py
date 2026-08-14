#!/usr/bin/env python3
"""
RugMunchBot -- Global error handler.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from telegram import Update

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception: {ctx.error}", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                "\u26a0\ufe0f Something went wrong. Please try again.\nIf the issue persists, contact support.",
                parse_mode="HTML",
            )
