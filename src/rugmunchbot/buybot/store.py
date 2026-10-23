"""Redis persistence for the per-chat buy-popup config.

Reads are fail-soft — a missing Redis, an absent document or a corrupt one is
a *disabled* popup, never an exception in a handler. Writes are fail-loud: the
admin gets ``(False, reason)``. The client is injected; ``None`` means the
process-wide async Redis singleton (same seam as ``community.store``).
"""

from __future__ import annotations

import json
from typing import Any

from rugmunchbot.buybot.models import CONFIG_KEY_PREFIX, BuybotConfig
from rugmunchbot.community.store import COMMUNITY_CLIENT_KEY, resolve_client
from rugmunchbot.support.logging import get_logger

__all__ = ["bot_client", "config_key", "delete_config", "load_config", "save_config"]

logger = get_logger("rmi_bot.buybot.store")


def bot_client(context: Any) -> Any:
    """Redis client injected through ``bot_data`` (``None`` = process singleton)."""
    store = getattr(getattr(context, "application", None), "bot_data", None) or {}
    return store.get(COMMUNITY_CLIENT_KEY)


def config_key(chat_id: int) -> str:
    """Redis key holding the popup document for *chat_id*."""
    return f"{CONFIG_KEY_PREFIX}{int(chat_id)}"


async def load_config(chat_id: int, client: Any = None) -> BuybotConfig:
    """Load the popup config for *chat_id*; defaults (disabled) when absent."""
    redis = resolve_client(client)
    if redis is None:
        logger.warning("buybot_load_redis_unavailable chat_id=%s", chat_id)
        return BuybotConfig(chat_id=chat_id)
    try:
        raw = await redis.get(config_key(chat_id))
    except Exception:  # a read failure must not break a command
        logger.warning("buybot_load_failed chat_id=%s", chat_id, exc_info=True)
        return BuybotConfig(chat_id=chat_id)
    if not raw:
        return BuybotConfig(chat_id=chat_id)
    try:
        payload = json.loads(raw)
        payload["chat_id"] = int(chat_id)
        return BuybotConfig.model_validate(payload)
    except (ValueError, TypeError):
        logger.warning("buybot_document_invalid chat_id=%s (using defaults)", chat_id)
        return BuybotConfig(chat_id=chat_id)


async def save_config(config: BuybotConfig, client: Any = None) -> tuple[bool, str | None]:
    """Persist *config*; returns ``(ok, reason)`` with a reason only on failure."""
    redis = resolve_client(client)
    if redis is None:
        logger.error("buybot_save_redis_unavailable chat_id=%s", config.chat_id)
        return False, "storage is unavailable right now — nothing was saved"
    payload = json.dumps(config.model_dump(mode="json"), separators=(",", ":"))
    try:
        await redis.set(config_key(config.chat_id), payload)
    except Exception:  # surfaced to the admin, never a silent no-op
        logger.error("buybot_save_failed chat_id=%s", config.chat_id, exc_info=True)
        return False, "storage rejected the write — nothing was saved"
    return True, None


async def delete_config(chat_id: int, client: Any = None) -> bool:
    """Delete the popup document (the chat falls back to disabled defaults)."""
    redis = resolve_client(client)
    if redis is None:
        return False
    try:
        await redis.delete(config_key(chat_id))
    except Exception:
        logger.error("buybot_delete_failed chat_id=%s", chat_id, exc_info=True)
        return False
    return True
