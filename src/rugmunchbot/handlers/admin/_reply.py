"""Owner gate and outbound reply helper shared by the admin commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode
from telegram.error import TelegramError

from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update

logger = get_logger("rmi_bot.admin")


def _deny(update: Update) -> bool:
    """Return True when the sender is NOT an owner (and log the attempt)."""
    user = update.effective_user
    if user is not None and is_owner(user.id):
        return False
    logger.warning(
        "admin_cmd_denied user=%s username=%s",
        user.id if user else None,
        user.username if user else None,
    )
    return True


async def _reply(update: Update, text: str) -> None:
    """Send a formatted HTML reply, retrying once as plain text on Telegram errors."""
    msg = update.effective_message
    if msg is None:
        return
    try:
        await msg.reply_text(text, parse_mode=ParseMode.HTML)
    except TelegramError as exc:
        logger.warning("admin_reply_failed err=%s", str(exc)[:120])
        try:
            await msg.reply_text(text)
        except TelegramError:
            logger.warning("admin_reply_plain_failed", exc_info=True)
