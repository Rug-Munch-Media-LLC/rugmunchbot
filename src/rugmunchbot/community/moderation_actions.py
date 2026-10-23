"""Apply one moderation decision to the injected client.

The only module that talks to the client, so every enforcement path — automatic
verdict and admin command alike — deletes, warns, mutes and bans the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rugmunchbot.community.moderation_models import ModerationClient, Policy

__all__ = ["act"]

LEVELS = ("warn", "mute", "ban")


async def act(
    policy: Policy,
    level: str,
    chat_id: int,
    user_id: int,
    client: ModerationClient,
    message_id: int = 0,
    *,
    now: float,
) -> str:
    """Perform *level* for one member and return the level actually applied."""
    chat_id, user_id = int(chat_id), int(user_id)
    if level != "warn" and message_id and policy.delete_on_action:
        await client.delete_message(chat_id, int(message_id))
    if level == "warn":
        await client.send_message(chat_id, f"⚠️ Warning for user {user_id}.")
    elif level == "mute":
        await client.restrict_member(chat_id, user_id, int(now) + policy.mute_seconds)
    else:
        await client.ban_member(chat_id, user_id)
    return level
