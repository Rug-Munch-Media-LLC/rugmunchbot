#!/usr/bin/env python3
"""
RugMunchBot — broadcast delivery worker.

Background job (registered in bot.py) that picks up queued broadcasts from
Redis (enqueued by the backend admin API) and delivers them to every
non-banned user, updating delivery state as it goes.

Fails open: if Redis or the bot DB is unavailable the job just skips the
current tick and retries on the next interval.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.admin_state import (
    pop_broadcast,
    update_broadcast,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

BROADCAST_SEND_DELAY = 0.05  # seconds between sends (Telegram rate-limit politeness)


async def deliver_pending_broadcasts(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Deliver every queued broadcast to all non-banned users.

    Returns the number of broadcasts fully processed this tick.
    """
    processed = 0
    while True:
        bcast = await pop_broadcast()
        if bcast is None:
            break
        processed += 1
        await _deliver_one(ctx, bcast)
    return processed


async def _deliver_one(ctx: ContextTypes.DEFAULT_TYPE, bcast: dict[str, object]) -> None:
    bcast_id = str(bcast.get("id") or "?")
    message = bcast.get("message") or ""
    if not isinstance(message, str):
        message = str(message)
    if not message:
        await update_broadcast(bcast_id, status="failed", error="empty_message")
        return

    try:
        conn = db.get_db()
        rows = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
        conn.close()
    except Exception as exc:
        logger.warning("broadcast: db unavailable for %s: %s", bcast_id, exc)
        await update_broadcast(bcast_id, status="failed", error="db_unavailable")
        return

    total = len(rows)
    sent = failed = 0
    await update_broadcast(bcast_id, status="sending", total=total)

    for row in rows:
        uid = str(row["user_id"])
        try:
            await ctx.bot.send_message(uid, message, parse_mode="HTML")
            sent += 1
        except (TelegramError, KeyError, TypeError) as exc:
            failed += 1
            logger.debug("broadcast: send failed to user=%s: %s", uid, exc)
        await asyncio.sleep(BROADCAST_SEND_DELAY)

    await update_broadcast(bcast_id, status="completed", sent=sent, failed=failed, total=total)
    logger.info("broadcast: %s delivered sent=%s failed=%s total=%s", bcast_id, sent, failed, total)
