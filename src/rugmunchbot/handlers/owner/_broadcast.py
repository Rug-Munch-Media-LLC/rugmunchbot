"""Owner/admin broadcast flow — preview, confirm callback, fan-out delivery.

Extracted verbatim from the former single-module ``handlers/owner.py``.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from rugmunchbot import db
from rugmunchbot.formatters import is_owner, thin_sep
from rugmunchbot.handlers.owner._shared import (
    _deny_admin_attempt,
    _log,
    _pending_broadcast,
)
from rugmunchbot.keyboards import admin_confirm_kb
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Bot, Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def cmd_admin_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_broadcast <message> — request confirmation, then broadcast."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_broadcast <message>")  # type: ignore[union-attr]  # PTB None-able fields
        return
    msg_text = " ".join(ctx.args)
    conn = db.get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 0").fetchone()["c"]
    conn.close()
    _pending_broadcast[uid] = msg_text
    _log(uid, "admin_broadcast_pending", f"recipients={count} preview={msg_text[:80]}")
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f4e2 <b>Broadcast Preview</b>\n{thin_sep()}\n\n{msg_text}\n\nSend this to <b>{count}</b> active users?",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_confirm_kb("broadcast"),
    )


async def _do_broadcast(bot: Bot, msg_text: str, owner_uid: int | None = None) -> tuple[int, int]:
    conn = db.get_db()
    users = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
    conn.close()
    sent = failed = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], msg_text, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            # Flood control: back off as Telegram instructs, retry once, then skip.
            retry_after = e.retry_after
            retry_secs = (
                retry_after.total_seconds()
                if isinstance(retry_after, timedelta)
                else float(retry_after)
            )
            await asyncio.sleep(retry_secs + 1)
            try:
                await bot.send_message(u["user_id"], msg_text, parse_mode=ParseMode.HTML)
                sent += 1
            except TelegramError as retry_err:
                logger.debug(f"Broadcast retry failed for user {u['user_id']}: {retry_err}")
                failed += 1
        except TelegramError as e:
            # Blocked (Forbidden), malformed (BadRequest), or transient API error:
            # skip this recipient and keep fanning out.
            logger.debug(f"Broadcast skipped for user {u['user_id']}: {e}")
            failed += 1
        except (KeyError, TypeError) as e:
            # Programming error: surface it, abort the fan-out.
            logger.debug(f"Broadcast aborted for user {u['user_id']}: {e}")
            raise
    if owner_uid is not None:
        _log(owner_uid, "admin_broadcast", f"sent={sent} failed={failed}")
    return sent, failed


# ── Callback helpers (used by handlers/callback.py) ──


async def cb_broadcast_confirm(
    owner_uid: int, chat_id: int, bot: Bot, message_id: int | None = None
) -> None:
    """Execute a confirmed broadcast."""
    msg_text = _pending_broadcast.pop(owner_uid, "")
    if not msg_text:
        return
    sent, failed = await _do_broadcast(bot, msg_text, owner_uid=owner_uid)
    try:
        await bot.send_message(
            chat_id,
            f"\U0001f4e2 Broadcast complete.\n\u2705 Sent: {sent}\n\u274c Failed: {failed}",
            parse_mode=ParseMode.HTML,
        )
    except (Forbidden, BadRequest, KeyError, TypeError):
        logger.debug("broadcast summary send failed chat=%s", chat_id)


def pending_broadcast(owner_uid: int) -> str | None:
    """Return the pending broadcast text for the owner, or None."""
    return _pending_broadcast.get(owner_uid)
