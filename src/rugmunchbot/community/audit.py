"""Audit trail for channel-owner settings changes.

Every successful ``/setup`` mutation appends one entry to a capped Redis list
per chat (newest first). The trail answers "who changed the token address and
when" without needing application logs. Audit failures are logged and
swallowed: a broken audit sink must never block a settings write.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from rugmunchbot.community.models import AUDIT_KEY_PREFIX
from rugmunchbot.community.store import resolve_client
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.community.audit")

#: Hard cap on retained entries per chat.
AUDIT_MAX_ENTRIES = 1000
#: Default page size for ``/setup audit``.
AUDIT_PAGE = 10


class AuditEntry(BaseModel):
    """One recorded settings change."""

    chat_id: int
    user_id: int
    key: str
    old_value: Any = None
    new_value: Any = None
    timestamp: float = Field(default_factory=time.time)


def audit_key(chat_id: int) -> str:
    """Redis key holding the audit list for *chat_id*."""
    return f"{AUDIT_KEY_PREFIX}{chat_id}"


async def append_audit(
    chat_id: int,
    user_id: int,
    key: str,
    old_value: Any = None,
    new_value: Any = None,
    client: Any = None,
) -> None:
    """Append one entry, trimming the list to :data:`AUDIT_MAX_ENTRIES`."""
    redis = resolve_client(client)
    if redis is None:
        logger.warning("audit_skipped_redis_unavailable chat_id=%s", chat_id)
        return
    entry = AuditEntry(
        chat_id=chat_id,
        user_id=user_id,
        key=key,
        old_value=old_value,
        new_value=new_value,
    )
    target = audit_key(chat_id)
    try:
        await redis.lpush(target, entry.model_dump_json())
        await redis.ltrim(target, 0, AUDIT_MAX_ENTRIES - 1)
    except Exception:  # audit must never break a settings write
        logger.warning("audit_append_failed chat_id=%s", chat_id, exc_info=True)


async def get_audit_log(
    chat_id: int,
    limit: int = AUDIT_PAGE,
    client: Any = None,
) -> list[AuditEntry]:
    """Return the most recent *limit* entries, newest first."""
    redis = resolve_client(client)
    if redis is None:
        return []
    limit = max(1, min(limit, AUDIT_MAX_ENTRIES))
    try:
        raw = await redis.lrange(audit_key(chat_id), 0, limit - 1)
    except Exception:  # a read failure degrades to "no history"
        logger.warning("audit_read_failed chat_id=%s", chat_id, exc_info=True)
        return []
    entries: list[AuditEntry] = []
    for item in raw:
        try:
            entries.append(AuditEntry.model_validate_json(item))
        except ValueError:
            logger.warning("audit_entry_corrupt chat_id=%s", chat_id)
    return entries


async def clear_audit(chat_id: int, client: Any = None) -> None:
    """Drop the audit list (used by ``/setup reset``)."""
    redis = resolve_client(client)
    if redis is None:
        return
    try:
        await redis.delete(audit_key(chat_id))
    except Exception:  # best-effort cleanup
        logger.warning("audit_clear_failed chat_id=%s", chat_id, exc_info=True)
