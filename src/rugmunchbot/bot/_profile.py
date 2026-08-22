"""Bot profile setup + daily scam-school tip job."""

from __future__ import annotations

import random
from datetime import time as dtime  # noqa: F401 — used by daily_scam_tip if re-enabled
from typing import TYPE_CHECKING, Any

from telegram.error import TelegramError

from rugmunchbot.bot._commands import (
    _BOT_COMMANDS,
    _COMMAND_SECTIONS,
)
from rugmunchbot.config import (
    BOT_DESCRIPTION,
    BOT_SHORT_DESCRIPTION,
    CHANNELS,
    SCAM_SCHOOL_TOPICS,
    WEBSITE_URL,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")

if TYPE_CHECKING:
    from telegram.ext import (
        Application,
        ContextTypes,
    )


async def setup_bot_profile(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Set bot description, about text, and command list via BotFather API."""
    bot = app.bot
    # Resolve the bot's own identity (get_me) so ``bot.username`` is populated
    # for the free-text group gate (@mention matching in _is_bot_addressed).
    # Fail-soft: if identity cannot be resolved, private DMs and /chat still
    # work and un-@mentioned group chatter stays ignored.
    try:
        await bot.get_me()
        logger.info("Bot identity resolved: @%s", bot.username)
    except Exception as e:
        logger.warning(f"Could not resolve bot identity (mention gate disabled): {e}")
    try:
        await bot.set_my_description(description=BOT_DESCRIPTION)
        logger.info("Bot description set")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("setup_bot_profile_error", exc_info=True)
        logger.warning(f"Could not set description: {e}")
    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        logger.info("Bot short description set")
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("setup_bot_profile_error", exc_info=True)
        logger.warning(f"Could not set short description: {e}")
    try:
        commands = _BOT_COMMANDS
        await bot.set_my_commands(commands)
        logger.info(
            "Bot commands registered (%d across %d sections)", len(commands), len(_COMMAND_SECTIONS)
        )
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.warning(f"Could not set commands: {e}")


async def daily_scam_tip(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not CHANNELS.get("main"):
        return
    topic_key = random.choice(list(SCAM_SCHOOL_TOPICS.keys()))
    topic = SCAM_SCHOOL_TOPICS[topic_key]
    sep_line = "\u2501" * 28
    text = (
        f"\U0001f6e1\ufe0f <b>Daily Scam Tip</b>\n{sep_line}\n\n"
        f"{topic['content']}\n\n"
        f"\U0001f4da Learn more: /scamschool\n"
        f"\U0001f50d Scan tokens: @RugMunchBot\n"
        f'\U0001f310 <a href="{WEBSITE_URL}">rugmunch.io</a>'
    )
    try:
        await ctx.bot.send_message(
            CHANNELS["main"], text, parse_mode="HTML", disable_web_page_preview=True
        )
    except TelegramError as e:
        # Channel perms / transient API error — log and let the next daily run retry.
        logger.error(f"Daily tip failed: {e}", exc_info=True)
