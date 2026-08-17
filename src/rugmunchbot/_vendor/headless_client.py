# VENDORED 2026-08-17 from rmi-backend/app/domains/telegram/headless_client.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Headless Telegram Client — Core Client
========================================
Manages a single Telegram user session via MTProto (Telethon)
for automated group management, messaging, and moderation.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.headless_credentials import get_api_credentials, get_session_path
from rugmunchbot._vendor.headless_models import GroupConfig, GroupInfo, GroupType

# Telethon is imported lazily to allow graceful fallback.
_telethon_available: bool | None = None

if TYPE_CHECKING:
    from telethon import TelegramClient
    from telethon.tl.types import User

logger = get_logger("telegram.headless.client")


class HeadlessTelegramClient:
    """Headless Telegram client using MTProto (Telethon).

    Manages a single Telegram user session for group management.
    Reuses session string across restarts to avoid re-authentication.
    """

    def __init__(self) -> None:
        self._client: TelegramClient | None = None
        self._phone: str = ""
        self._api_id: int = 0
        self._api_hash: str = ""
        self._me: User | None = None
        self._rate_limit_delay: float = 1.0
        self._last_call: float = 0.0
        self._managed_groups: dict[str, GroupInfo] = {}

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected()

    @property
    def me(self) -> User | None:
        return self._me

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize and start the Telethon client.

        Authenticates using stored session or via gopass credentials.
        """
        global _telethon_available

        # Re-probe on every failed availability check (not just the first):
        # the bot may boot before telethon is pip-installed (seen in prod —
        # the process started 5.5h before the dep landed, cached False, and
        # every tg_news sweep then failed forever until a restart). Once a
        # probe succeeds the positive result is kept (no import churn).
        if _telethon_available is not True:
            try:
                import telethon  # noqa: F401 — availability probe

                _telethon_available = True
            except ImportError:
                _telethon_available = False

        if not _telethon_available:
            raise RuntimeError("Telethon not installed. Run: pip install telethon")

        from telethon import TelegramClient

        self._phone, self._api_id, self._api_hash = get_api_credentials()

        session_path = get_session_path()
        os.makedirs(os.path.dirname(session_path), exist_ok=True)

        self._client = TelegramClient(
            session_path,
            self._api_id,
            self._api_hash,
            flood_sleep_threshold=60,
            request_retries=5,
            connection_retries=3,
            retry_delay=2,
        )

        await self._client.start(phone=self._phone)
        self._me = await self._client.get_me()
        logger.info(
            "headless_client_started user=%s user_id=%d",
            self._me.username or self._me.first_name,
            self._me.id,
        )

    async def stop(self) -> None:
        """Disconnect the client gracefully (idempotent, bounded).

        Telethon's ``disconnect()`` is safe to call repeatedly and also closes
        the underlying MTProto connection + session. It is bounded with a
        timeout so a wedged socket can never block process shutdown.
        """
        client = self._client
        if client is None:
            return
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5)
            logger.info("headless_client_stopped")
        except TimeoutError:
            logger.warning("headless_client_stop_timeout (force-closing connection)")
        except Exception:
            logger.warning("headless_client_stop_error", exc_info=True)
        finally:
            self._client = None
            self._me = None

    async def _rate_limit(self) -> None:
        """Ensure we don't exceed Telegram's rate limits."""
        elapsed = time.time() - self._last_call
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_call = time.time()

    # ── Group Management ──────────────────────────────────────────────

    async def create_group(self, config: GroupConfig) -> GroupInfo:
        """Create a new Telegram group.

        Args:
            config: Group configuration including title, type, permissions.

        Returns:
            GroupInfo with chat_id, invite link, etc.

        Raises:
            RuntimeError: If group creation fails.
        """
        if not self._client:
            raise RuntimeError("Client not started. Call start() first.")

        from telethon.tl.functions.messages import CreateChatRequest
        from telethon.tl.types import InputPeerEmpty

        await self._rate_limit()

        try:
            result = await self._client(
                CreateChatRequest(
                    users=[InputPeerEmpty()],
                    title=config.title,
                )
            )

            chat_id = None
            for update in result.updates:
                if hasattr(update, "chat_id"):
                    chat_id = update.chat_id
                    break

            if not chat_id:
                for update in result.updates:
                    if hasattr(update, "message") and hasattr(update.message, "peer_id"):
                        chat_id = update.message.peer_id.chat_id
                        break

            if not chat_id:
                raise RuntimeError("Could not determine chat ID from create result")

            if config.is_public or config.username:
                await self._migrate_to_supergroup(chat_id, config)

            if config.description:
                await self._set_group_description(chat_id, config.description)

            await self._configure_group_permissions(chat_id, config)

            await self._get_invite_link(chat_id)  # pre-warm invite link

            if config.creator_user_id:
                await self._add_group_admin(chat_id, config.creator_user_id)

            if config.username and config.is_public:
                await self._set_group_username(chat_id, config.username)

            group_info = await self._fetch_group_info(chat_id)
            group_info.group_type = config.group_type

            key = config.username or str(chat_id)
            self._managed_groups[key] = group_info

            logger.info(
                "group_created chat_id=%d title=%s type=%s",
                chat_id,
                config.title,
                config.group_type.value,
            )
            return group_info

        except Exception as e:
            logger.error("group_create_failed title=%s err=%s", config.title, e)
            raise RuntimeError(f"Failed to create group: {e}") from e

    async def _migrate_to_supergroup(self, chat_id: int, config: GroupConfig) -> int:
        """Migrate a basic group to a supergroup."""
        if not self._client:
            raise RuntimeError("Client not started")

        from telethon.tl.functions.messages import MigrateChatRequest

        await self._rate_limit()

        try:
            result = await self._client(MigrateChatRequest(chat_id=chat_id))
            for update in result.updates:
                if hasattr(update, "channel_id"):
                    return update.channel_id
            logger.warning("Migration result did not contain channel_id, using original ID")
            return chat_id
        except Exception as e:
            logger.warning("Supergroup migration failed (non-fatal): %s", e)
            return chat_id

    async def _set_group_description(self, chat_id: int, description: str) -> None:
        """Set the group description/about text."""
        if not self._client:
            return

        from telethon.tl.functions.messages import EditChatAboutRequest

        await self._rate_limit()
        try:
            await self._client(EditChatAboutRequest(peer=chat_id, about=description))
            logger.debug("Group description set for %d", chat_id)
        except Exception as e:
            logger.warning("Could not set group description: %s", e)

    async def _configure_group_permissions(self, chat_id: int, config: GroupConfig) -> None:
        """Configure group permissions (slow mode, join mode, etc.)."""
        if not self._client:
            return

        from telethon.tl.functions.messages import EditChatDefaultBannedRightsRequest
        from telethon.tl.types import ChatBannedRights

        banned_rights = ChatBannedRights(
            until_date=datetime(2038, 1, 1, tzinfo=UTC),
            send_messages=False,
            send_media=False,
            send_stickers=False,
            send_gifs=False,
            send_games=False,
            send_inline=False,
            embed_links=False,
            send_polls=False,
            change_info=True,
            invite_users=False,
            pin_messages=True,
        )

        await self._rate_limit()
        try:
            await self._client(
                EditChatDefaultBannedRightsRequest(
                    peer=chat_id,
                    banned_rights=banned_rights,
                )
            )
            logger.debug("Group permissions configured for %d", chat_id)
        except Exception as e:
            logger.warning("Could not configure group permissions: %s", e)

    async def _get_invite_link(self, chat_id: int) -> str | None:
        """Get or create an invite link for the group."""
        if not self._client:
            return None

        from telethon.tl.functions.messages import ExportChatInviteRequest

        await self._rate_limit()
        try:
            result = await self._client(
                ExportChatInviteRequest(
                    peer=chat_id,
                    usage_limit=0,
                    title="Default invite",
                )
            )
            return result.link if hasattr(result, "link") else None
        except Exception as e:
            logger.warning("Could not get invite link: %s", e)
            return None

    async def _set_group_username(self, chat_id: int, username: str) -> None:
        """Set a public username for the group (supergroups only)."""
        if not self._client:
            return

        from telethon.tl.functions.channels import UpdateUsernameRequest

        await self._rate_limit()
        try:
            await self._client(
                UpdateUsernameRequest(
                    channel=chat_id,
                    username=username.lstrip("@"),
                )
            )
            logger.info("Group username set: @%s", username)
        except Exception as e:
            logger.warning("Could not set group username: %s", e)

    async def _add_group_admin(self, chat_id: int, user_id: int) -> None:
        """Add a user as admin in the group."""
        if not self._client:
            return

        from telethon.tl.functions.channels import EditAdminRequest, InviteToChannelRequest
        from telethon.tl.types import ChatAdminRights

        admin_rights = ChatAdminRights(
            change_info=True,
            post_messages=True,
            edit_messages=True,
            delete_messages=True,
            ban_users=True,
            invite_users=True,
            pin_messages=True,
            add_admins=False,
            anonymous=False,
            manage_call=True,
            other=True,
        )

        await self._rate_limit()
        try:
            await self._client(InviteToChannelRequest(channel=chat_id, users=[user_id]))

            await self._client(
                EditAdminRequest(
                    channel=chat_id,
                    user_id=user_id,
                    admin_rights=admin_rights,
                    rank="Creator",
                )
            )
            logger.info("User %d added as admin to %d", user_id, chat_id)
        except Exception as e:
            logger.warning("Could not add admin: %s", e)

    async def _fetch_group_info(self, chat_id: int) -> GroupInfo:
        """Fetch current group information."""
        if not self._client:
            raise RuntimeError("Client not started")

        await self._rate_limit()
        try:
            full = await self._client.get_entity(chat_id)
            username = getattr(full, "username", None)
            title = getattr(full, "title", getattr(full, "name", "Unknown"))
            member_count = getattr(full, "participants_count", 0)

            invite_link = await self._get_invite_link(chat_id)

            return GroupInfo(
                chat_id=chat_id,
                title=title,
                username=username,
                invite_link=invite_link,
                member_count=member_count,
                is_public=username is not None,
                group_type=GroupType.COMMUNITY,
            )
        except Exception as e:
            logger.error("Could not fetch group info: %s", e)
            raise

    # ── Messaging ─────────────────────────────────────────────────────

    async def send_message(
        self,
        chat_id: int,
        message: str,
        *,
        parse_mode: str = "markdown",
        link_preview: bool = False,
        pin: bool = False,
    ) -> bool:
        """Send a message to a group.

        Args:
            chat_id: Target group chat ID.
            message: Message text (supports HTML/Markdown).
            parse_mode: 'markdown' or 'html'.
            link_preview: Whether to show link previews.
            pin: Whether to pin the message.

        Returns:
            True if sent successfully.
        """
        if not self._client:
            raise RuntimeError("Client not started")

        from telethon.tl.functions.messages import PinMessageRequest

        await self._rate_limit()
        try:
            msg = await self._client.send_message(
                entity=chat_id,
                message=message,
                parse_mode=parse_mode,
                link_preview=link_preview,
            )
            if pin and msg:
                try:
                    await self._client(PinMessageRequest(peer=chat_id, id=msg.id))
                except Exception:
                    logger.debug("Could not pin message")
            return True
        except Exception as e:
            logger.error("Could not send message to %d: %s", chat_id, e)
            return False

    async def send_photo(self, chat_id: int, photo_path: str, caption: str | None = None) -> bool:
        """Send a photo to a group."""
        if not self._client:
            raise RuntimeError("Client not started")

        await self._rate_limit()
        try:
            await self._client.send_file(entity=chat_id, file=photo_path, caption=caption)
            return True
        except Exception as e:
            logger.error("Could not send photo: %s", e)
            return False

    # ── Moderation ────────────────────────────────────────────────────

    async def ban_user(
        self,
        chat_id: int,
        user_id: int,
        *,
        reason: str | None = None,
        revoke_messages: bool = True,
    ) -> bool:
        """Ban a user from a group.

        Args:
            chat_id: Group chat ID.
            user_id: User ID to ban.
            reason: Optional ban reason (logged only).
            revoke_messages: Remove user's messages.

        Returns:
            True if banned successfully.
        """
        if not self._client:
            raise RuntimeError("Client not started")

        from telethon.tl.functions.channels import EditBannedRequest
        from telethon.tl.types import ChatBannedRights

        await self._rate_limit()

        banned_rights = ChatBannedRights(
            until_date=datetime(2038, 1, 1, tzinfo=UTC),
            view_messages=True,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_links=True,
            send_polls=True,
            change_info=True,
            invite_users=True,
            pin_messages=True,
        )

        try:
            await self._client(
                EditBannedRequest(
                    channel=chat_id,
                    participant=user_id,
                    banned_rights=banned_rights,
                )
            )
            if reason:
                logger.info("User %d banned from %d reason=%s", user_id, chat_id, reason)
            else:
                logger.info("User %d banned from %d", user_id, chat_id)
            return True
        except Exception as e:
            logger.error("Could not ban user %d: %s", user_id, e)
            return False

    async def unban_user(self, chat_id: int, user_id: int) -> bool:
        """Unban a user from a group."""
        if not self._client:
            raise RuntimeError("Client not started")

        from telethon.tl.functions.channels import EditBannedRequest
        from telethon.tl.types import ChatBannedRights

        await self._rate_limit()

        banned_rights = ChatBannedRights(
            until_date=datetime(1970, 1, 1, tzinfo=UTC),
            view_messages=False,
            send_messages=False,
            send_media=False,
            send_stickers=False,
            send_gifs=False,
            send_games=False,
            send_inline=False,
            embed_links=False,
            send_polls=False,
            change_info=False,
            invite_users=False,
            pin_messages=False,
        )

        try:
            await self._client(
                EditBannedRequest(
                    channel=chat_id,
                    participant=user_id,
                    banned_rights=banned_rights,
                )
            )
            logger.info("User %d unbanned from %d", user_id, chat_id)
            return True
        except Exception as e:
            logger.error("Could not unban user %d: %s", user_id, e)
            return False

    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Delete a specific message from a group."""
        if not self._client:
            raise RuntimeError("Client not started")

        await self._rate_limit()
        try:
            await self._client.delete_messages(chat_id, [message_id])
            return True
        except Exception as e:
            logger.error("Could not delete message: %s", e)
            return False

    # ── Voice Chat Management ─────────────────────────────────────────

    async def start_voice_chat(
        self,
        chat_id: int,
        *,
        title: str | None = None,
        is_video: bool = False,
    ) -> bool:
        """Start a voice/video chat in the group."""
        if not self._client:
            raise RuntimeError("Client not started")

        from rugmunchbot.config import BOT_TOKEN  # Phase 3: own repo

        from rugmunchbot._vendor.voice_chat import create_voice_chat

        if not BOT_TOKEN:
            logger.error("BOT_TOKEN not available for voice chat")
            return False

        try:
            from telegram import Bot

            bot = Bot(token=BOT_TOKEN)
            session = await create_voice_chat(bot, chat_id, title=title, is_video=is_video)
            logger.info("voice_chat_started chat_id=%d invite=%s", chat_id, session.invite_link)
            return True
        except Exception as e:
            logger.error("Could not start voice chat: %s", e)
            return False

    async def end_voice_chat(self, chat_id: int) -> bool:
        """End an active voice/video chat."""
        from rugmunchbot.config import BOT_TOKEN  # Phase 3: own repo

        from rugmunchbot._vendor.voice_chat import end_voice_chat

        if not BOT_TOKEN:
            logger.error("BOT_TOKEN not available")
            return False

        try:
            from telegram import Bot

            bot = Bot(token=BOT_TOKEN)
            return await end_voice_chat(bot, chat_id)
        except Exception as e:
            logger.error("Could not end voice chat: %s", e)
            return False

    # ── Auto-provisioning for Creator Coins ───────────────────────────

    async def provision_creator_coin_group(
        self,
        creator_name: str,
        coin_ticker: str,
        creator_user_id: int,
    ) -> GroupInfo:
        """Provision a Telegram group for a new creator coin."""
        group_title = f"{creator_name}'s Coin \u2014 ${coin_ticker}"
        description = (
            f"\U0001f3db\ufe0f Official community for {creator_name}'s coin.\n\n"
            f"\U0001f4b0 Ticker: ${coin_ticker}\n"
            f"\U0001f4ca Track price, join voice chats, and discuss the project.\n\n"
            f"\U0001f916 Powered by RugMunch Intelligence"
        )
        username_base = coin_ticker.lower().replace(" ", "")
        username = f"RM{username_base}"

        config = GroupConfig(
            group_type=GroupType.CREATOR_COIN,
            title=group_title,
            description=description,
            username=username,
            is_public=True,
            slow_mode_seconds=5,
            join_by_request=True,
            creator_user_id=creator_user_id,
            metadata={"creator_name": creator_name, "coin_ticker": coin_ticker},
        )

        return await self.create_group(config)

    async def post_coin_update(
        self,
        chat_id: int,
        coin_ticker: str,
        price_usd: float | None = None,
        market_cap: float | None = None,
        volume_24h: float | None = None,
        change_24h: float | None = None,
    ) -> bool:
        """Post a coin price update to the group."""
        message_parts = [f"\U0001f4ca **${coin_ticker} Update**\n"]

        if price_usd is not None:
            message_parts.append(f"\U0001f4b0 Price: `${price_usd:.8f}`")
        if market_cap is not None:
            message_parts.append(f"\U0001f3e6 Market Cap: `${market_cap:,.0f}`")
        if volume_24h is not None:
            message_parts.append(f"\U0001f4c8 24h Volume: `${volume_24h:,.0f}`")
        if change_24h is not None:
            emoji = "\U0001f7e2" if change_24h >= 0 else "\U0001f534"
            message_parts.append(f"{emoji} 24h Change: `{change_24h:+.2f}%`")

        message_parts.append("\n\U0001f916 _Powered by RugMunch Intelligence_")

        return await self.send_message(chat_id, "\n".join(message_parts))
