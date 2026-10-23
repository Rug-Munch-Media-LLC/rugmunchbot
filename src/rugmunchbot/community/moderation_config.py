"""Per-chat moderation policy in Redis: fail-soft reads, fail-loud writes.

One JSON document per chat. A read that cannot be served — no Redis, no
document, a corrupt document — yields the inert default :class:`Policy`, so an
outage degrades moderation to observe-only instead of enforcing stale rules.
Writes report failure; they never pretend to have saved.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from rugmunchbot.community.moderation_models import Policy
from rugmunchbot.community.moderation_policy import policy_from_mapping, policy_to_mapping
from rugmunchbot.community.store import resolve_client
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot.community.moderation import ModerationEngine

__all__ = ["POLICY_KEY_PREFIX", "load_policy", "policy_key", "save_policy", "sync_policy"]

logger = get_logger("rmi_bot.community.moderation_config")

POLICY_KEY_PREFIX = "community:moderation:policy:"


def policy_key(chat_id: int) -> str:
    """Redis key holding the moderation policy for *chat_id*."""
    return f"{POLICY_KEY_PREFIX}{int(chat_id)}"


async def load_policy(chat_id: int, client: Any = None) -> Policy | None:
    """Return the stored policy for *chat_id*.

    ``None`` means "unknown" — no client was supplied. A read failure, a chat
    with no document, or a document that will not parse yields the inert
    default instead, so an outage degrades to observe-only.
    """
    redis = resolve_client(client)
    if redis is None:
        logger.warning("moderation_policy_load_redis_unavailable chat_id=%s", chat_id)
        return None
    try:
        raw = await redis.get(policy_key(chat_id))
    except Exception:  # a read failure must never break a command
        logger.warning("moderation_policy_load_failed chat_id=%s", chat_id, exc_info=True)
        return Policy()
    if not raw:
        return Policy()
    try:
        return policy_from_mapping(json.loads(raw))
    except (ValueError, TypeError):
        logger.warning("moderation_policy_invalid chat_id=%s (using inert defaults)", chat_id)
        return Policy()


async def save_policy(chat_id: int, policy: Policy, client: Any = None) -> bool:
    """Persist *policy*; ``True`` only when Redis acknowledged the write."""
    redis = resolve_client(client)
    if redis is None:
        logger.error("moderation_policy_save_redis_unavailable chat_id=%s", chat_id)
        return False
    canonical = policy_from_mapping(policy_to_mapping(policy))
    payload = json.dumps(policy_to_mapping(canonical), separators=(",", ":"))
    try:
        await redis.set(policy_key(chat_id), payload)
    except Exception:  # a write failure must be visible to the caller
        logger.error("moderation_policy_save_failed chat_id=%s", chat_id, exc_info=True)
        return False
    return True


async def sync_policy(engine: ModerationEngine, chat_id: int, client: Any = None) -> Policy:
    """Install the stored policy on *engine*; an unreadable store changes nothing.

    Returns the policy the chat now runs under, so a caller can report the mode
    without a second read.
    """
    policy = await load_policy(chat_id, client)
    if policy is not None:
        engine.use_policy(chat_id, policy)
    return engine.policy_for(chat_id)
