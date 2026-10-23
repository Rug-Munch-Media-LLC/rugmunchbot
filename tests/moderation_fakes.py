"""Shared offline fakes for the group-moderation tests.

No network and no Telegram: Redis is an in-memory dict, the client records
calls, and the engine clock is fixed so audit rows are reproducible.
"""

from __future__ import annotations

from rugmunchbot.community.moderation import MessageEvent
from rugmunchbot.community.moderation_models import Policy

CHAT = -1001234567890
OTHER = -1009999
USER = 4242
SPAM = "claim your airdrop at https://drainer-claim.xyz/claim"
LIVE = Policy(dry_run=False, allow_auto_ban=True, strikes_mute=1, strikes_ban=2)


class FakeRedis:
    """Minimal async Redis; ``broken=True`` stands in for an outage."""

    def __init__(self, broken: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.broken = broken

    async def get(self, key: str) -> str | None:
        if self.broken:
            raise RuntimeError("redis down")
        return self.values.get(key)

    async def set(self, key: str, value: str) -> bool:
        if self.broken:
            raise RuntimeError("redis down")
        self.values[key] = value
        return True


class FakeClient:
    """Records every action instead of talking to Telegram."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.calls.append(("delete", chat_id, message_id))

    async def restrict_member(self, chat_id: int, user_id: int, until_date: int) -> None:
        self.calls.append(("mute", chat_id, user_id))

    async def ban_member(self, chat_id: int, user_id: int) -> None:
        self.calls.append(("ban", chat_id, user_id))

    async def send_message(self, chat_id: int, text: str) -> None:
        self.calls.append(("say", chat_id, text))


def event(chat_id: int = CHAT) -> MessageEvent:
    """One spam message from :data:`USER` in *chat_id*."""
    return MessageEvent(chat_id=chat_id, user_id=USER, message_id=77, text=SPAM)
