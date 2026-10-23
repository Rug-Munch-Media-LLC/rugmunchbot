"""Redis persistence for per-chat community settings.

One JSON document per chat. Reads are fail-soft: a missing Redis or a corrupt
document yields defaults rather than an exception, so a Redis outage degrades
the feature instead of breaking the bot. Writes are fail-loud: the caller gets
``False`` and an error message, never a silent no-op.

The Redis client is injected, never looked up globally inside the functions:
``/setup`` passes the client from ``bot_data`` when one is configured (tests,
multi-tenant runs), and ``None`` means "use the process singleton".
"""

from __future__ import annotations

import json
import time
from typing import Any

from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot.community.config_models import CommunitySettings
from rugmunchbot.community.models import SETTINGS_KEY_PREFIX
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.community.store")

#: ``Application.bot_data`` key holding an injected Redis-compatible client.
COMMUNITY_CLIENT_KEY = "community_redis_client"


def resolve_client(client: Any = None) -> Any:
    """Return the injected client, or the process-wide async Redis singleton."""
    return client if client is not None else get_redis_async()


def settings_key(chat_id: int) -> str:
    """Redis key holding the settings document for *chat_id*."""
    return f"{SETTINGS_KEY_PREFIX}{chat_id}"


async def load_settings(chat_id: int, client: Any = None) -> CommunitySettings:
    """Load settings for *chat_id*, returning defaults when absent."""
    redis = resolve_client(client)
    if redis is None:
        logger.warning("settings_load_redis_unavailable chat_id=%s", chat_id)
        return CommunitySettings(chat_id=chat_id)
    try:
        raw = await redis.get(settings_key(chat_id))
    except Exception:  # a read failure must not break a command
        logger.warning("settings_load_failed chat_id=%s", chat_id, exc_info=True)
        return CommunitySettings(chat_id=chat_id)
    if not raw:
        return CommunitySettings(chat_id=chat_id)
    try:
        return CommunitySettings.from_storage(chat_id, json.loads(raw))
    except (ValueError, TypeError):
        logger.warning("settings_document_invalid chat_id=%s (using defaults)", chat_id)
        return CommunitySettings(chat_id=chat_id)


async def save_settings(settings: CommunitySettings, client: Any = None) -> bool:
    """Persist *settings*; returns ``True`` only when Redis acknowledged it."""
    redis = resolve_client(client)
    if redis is None:
        logger.error("settings_save_redis_unavailable chat_id=%s", settings.chat_id)
        return False
    settings.updated_at = time.time()
    payload = json.dumps(settings.to_storage(), separators=(",", ":"))
    try:
        await redis.set(settings_key(settings.chat_id), payload)
    except Exception:  # surfaced to the caller as False
        logger.error("settings_save_failed chat_id=%s", settings.chat_id, exc_info=True)
        return False
    return True


async def delete_settings(chat_id: int, client: Any = None) -> bool:
    """Delete the settings document so the chat falls back to defaults."""
    redis = resolve_client(client)
    if redis is None:
        return False
    try:
        await redis.delete(settings_key(chat_id))
    except Exception:  # surfaced to the caller as False
        logger.error("settings_delete_failed chat_id=%s", chat_id, exc_info=True)
        return False
    return True
