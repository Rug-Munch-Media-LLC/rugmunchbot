"""Resolve the per-channel gate role that owns a chat id.

The live gate (``rugmunchbot._vendor.channel_gate``) is keyed by ROLE, while
every poster in this package is keyed by CHAT ID. This module is the only
bridge between the two, so a send can ask "am I allowed to post here?" with
one call and no duplicated role table.
"""

from __future__ import annotations

import logging

from rugmunchbot.channels.scheduler._core import _chat_id

logger = logging.getLogger(__name__)

# Registry roles, narrowest-surface last. Order only affects tie-breaking.
_GATE_ROLES: tuple[str, ...] = ("news", "main", "alpha", "alerts")


def role_for_chat_id(chat_id: object) -> str | None:
    """Gate role whose resolved chat id equals ``chat_id``, else ``None``.

    ``None`` means "no role owns this chat" (unknown channel or a raw numeric
    id nobody registered) — callers must then fall back to their own policy,
    never to "assume live". Resolution is best-effort and never raises:
    a gopass/env hiccup for one role simply skips that role.
    """
    target = str(chat_id or "").strip().lstrip("@").lower()
    if not target:
        return None
    for role in _GATE_ROLES:
        try:
            resolved = _chat_id(role)
        except Exception as exc:
            logger.debug("channel_role:resolve_failed role=%s err=%s", role, exc)
            continue
        if resolved and str(resolved).strip().lstrip("@").lower() == target:
            return role
    return None
