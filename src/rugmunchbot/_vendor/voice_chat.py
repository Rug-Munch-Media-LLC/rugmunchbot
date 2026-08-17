# VENDORED 2026-08-17 from rmi-backend/app/domains/telegram/voice_chat.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Telegram Voice/Video Chat Management
=====================================
Create, manage, and end voice/video chats in Telegram groups.
Uses python-telegram-bot's API to manage group voice chats.

Telegram bots can:
  - Create voice chats in groups where they are admin
  - End active voice chats
  - Get voice chat invite links
  - Monitor voice chat participant counts

Note: Bot-based voice chat management requires the bot to have
admin privileges in the target group with "Manage Voice Chats" permission.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from telegram import Bot
    from telegram._utils.types import JSONValue  # type: ignore[attr-defined]  # PTB None-able fields

logger = get_logger("telegram.voice_chat")

# ── In-memory voice chat state ──────────────────────────────────────────
# Tracks active voice chats per chat_id so we can manage state across
# multiple bot instances and API calls.
_active_voice_chats: dict[int, VoiceChatSession] = {}

MAX_DURATION_SECONDS = 28800  # 8 hours max (Telegram limit)


@dataclass
class VoiceChatSession:
    """Represents an active voice/video chat session."""

    chat_id: int
    title: str | None = None
    is_video: bool = False
    started_at: float = field(default_factory=time.time)
    participant_count: int = 0
    invite_link: str | None = None

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def is_expired(self) -> bool:
        return self.duration_seconds > MAX_DURATION_SECONDS


async def create_voice_chat(
    bot: Bot,
    chat_id: int,
    *,
    title: str | None = None,
    is_video: bool = False,
) -> VoiceChatSession:
    """Create a voice/video chat in a Telegram group.

    Args:
        bot: Authenticated Telegram Bot instance.
        chat_id: Target group chat ID (can be negative for supergroups).
        title: Optional title for the voice chat (shown in UI).
        is_video: If True, creates a video chat instead of voice-only.

    Returns:
        VoiceChatSession with invite link and metadata.

    Raises:
        RuntimeError: If bot lacks permissions or API call fails.
    """
    logger.info(
        "create_voice_chat chat_id=%d title=%s is_video=%s",
        chat_id,
        title,
        is_video,
    )

    # Telegram Bot API 7.0+ supports create_voice_chat via set_chat_permissions
    # For voice chat creation, we use edit_forum_topic-like approach via
    # the Telegram API's native voice chat methods.
    try:
        # Step 1: Verify bot is admin in the chat
        chat_member = await bot.get_chat_member(chat_id, bot.id)
        if chat_member.status not in ("administrator", "creator"):
            raise RuntimeError(f"Bot is not admin in chat {chat_id}. Status: {chat_member.status}")

        # Step 2: Create the voice chat using Telegram API
        # Guard: python-telegram-bot's ``Bot`` has no ``create_voice_chat``
        # method (voice-chat creation is not part of the Bot API) — fail with
        # a clear error instead of calling a nonexistent attribute.
        if not hasattr(bot, "create_voice_chat"):
            raise RuntimeError(
                "Bot.create_voice_chat is not available in this python-telegram-bot "
                "version — voice chat creation is not supported."
            )
        # Voice chats are created via the underlying API call
        result: JSONValue = await bot.create_voice_chat(  # PTB None-able fields
            chat_id=chat_id,
            voice_chat_title=title or "",
            voice_chat_video_chat=is_video,
        )

        if result is True or (isinstance(result, dict) and result.get("ok")):
            # Get invite link for the voice chat
            invite_link = None
            try:
                chat = await bot.get_chat(chat_id)
                if hasattr(chat, "invite_link") and chat.invite_link:
                    invite_link = chat.invite_link
            except Exception as e:
                logger.debug("Could not fetch invite link: %s", e)

            session = VoiceChatSession(
                chat_id=chat_id,
                title=title,
                is_video=is_video,
                invite_link=invite_link,
            )
            _active_voice_chats[chat_id] = session
            logger.info(
                "voice_chat_created chat_id=%d invite_link=%s",
                chat_id,
                invite_link,
            )
            return session
        else:
            raise RuntimeError(f"Failed to create voice chat in {chat_id}: {result}")

    except Exception as e:
        logger.error(
            "voice_chat_create_failed chat_id=%d err=%s",
            chat_id,
            e,
        )
        raise RuntimeError(f"Could not create voice chat: {e}") from e


async def end_voice_chat(bot: Bot, chat_id: int) -> bool:
    """End an active voice/video chat in a Telegram group.

    Args:
        bot: Authenticated Telegram Bot instance.
        chat_id: Target group chat ID.

    Returns:
        True if voice chat was successfully ended.

    Raises:
        RuntimeError: If no active voice chat exists or API call fails.
    """
    if chat_id not in _active_voice_chats:
        session_list = await list_voice_chats(bot)
        if chat_id not in session_list:
            raise RuntimeError(f"No active voice chat found in chat {chat_id}")

    logger.info("end_voice_chat chat_id=%d", chat_id)

    try:
        # Guard: python-telegram-bot's ``Bot`` has no ``end_voice_chat``
        # method — fail with a clear error instead of calling a nonexistent
        # attribute (see create_voice_chat for the same guard).
        if not hasattr(bot, "end_voice_chat"):
            raise RuntimeError(
                "Bot.end_voice_chat is not available in this python-telegram-bot "
                "version — voice chat ending is not supported."
            )
        result: JSONValue = await bot.end_voice_chat(  # PTB None-able fields
            chat_id=chat_id,
        )

        if result is True or (isinstance(result, dict) and result.get("ok")):
            _active_voice_chats.pop(chat_id, None)
            logger.info("voice_chat_ended chat_id=%d", chat_id)
            return True
        else:
            raise RuntimeError(f"Failed to end voice chat in {chat_id}: {result}")

    except Exception as e:
        logger.error(
            "voice_chat_end_failed chat_id=%d err=%s",
            chat_id,
            e,
        )
        raise RuntimeError(f"Could not end voice chat: {e}") from e


async def get_voice_chat_status(bot: Bot, chat_id: int) -> VoiceChatSession | None:
    """Get the current status of a voice/video chat.

    Args:
        bot: Authenticated Telegram Bot instance.
        chat_id: Target group chat ID.

    Returns:
        VoiceChatSession if active, None otherwise.
    """
    # First check our in-memory state
    session = _active_voice_chats.get(chat_id)

    # Verify with actual Telegram API
    try:
        chat = await bot.get_chat(chat_id)
        # Telegram returns voice_chat info in chat object
        if hasattr(chat, "voice_chat") and chat.voice_chat:
            if session is None:
                session = VoiceChatSession(
                    chat_id=chat_id,
                    invite_link=getattr(chat, "invite_link", None),
                )
                _active_voice_chats[chat_id] = session
            # Update participant count if available
            if hasattr(chat.voice_chat, "participant_count"):
                session.participant_count = chat.voice_chat.participant_count
            return session
    except Exception as e:
        logger.debug("get_voice_chat_status error: %s", e)

    # Check if expired
    if session and session.is_expired:
        _active_voice_chats.pop(chat_id, None)
        return None

    return session


async def list_voice_chats(bot: Bot) -> dict[int, VoiceChatSession]:
    """List all currently tracked active voice chats.

    Checks each tracked chat against Telegram API to verify
    the voice chat is still active.

    Args:
        bot: Authenticated Telegram Bot instance.

    Returns:
        Dict of chat_id -> VoiceChatSession for active chats.
    """
    active: dict[int, VoiceChatSession] = {}
    for chat_id in list(_active_voice_chats.keys()):
        session = await get_voice_chat_status(bot, chat_id)
        if session:
            active[chat_id] = session
    return active


async def get_voice_chat_invite_link(bot: Bot, chat_id: int) -> str | None:
    """Get or create an invite link for a voice chat.

    Args:
        bot: Authenticated Telegram Bot instance.
        chat_id: Target group chat ID.

    Returns:
        Invite link string or None if not available.
    """
    try:
        chat = await bot.get_chat(chat_id)
        link: str | None = getattr(chat, "invite_link", None)
        if link:
            return link

        # Create a temporary invite link if none exists
        invite = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=100,
            name="Voice Chat",
        )
        return invite.invite_link
    except Exception as e:
        logger.warning("Could not get invite link for %d: %s", chat_id, e)
        return None
