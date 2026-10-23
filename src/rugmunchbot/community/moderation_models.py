"""Value objects and the injectable client protocol for group moderation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = ["MessageEvent", "ModerationClient", "Policy", "Verdict"]


@dataclass(frozen=True, slots=True)
class Policy:
    """Per-chat configuration. Every field defaults to the inert setting."""

    dry_run: bool = True
    allow_auto_ban: bool = False
    strikes_mute: int = 3
    strikes_ban: int = 5
    mute_seconds: int = 3600
    max_mentions: int = 8
    duplicate_threshold: int = 3
    duplicate_window_s: int = 120
    new_account_days: int = 0
    trusted_hosts: tuple[str, ...] = ()
    delete_on_action: bool = True


@dataclass(slots=True)
class MessageEvent:
    """One inbound group message, independent of any Telegram object."""

    chat_id: int
    user_id: int
    message_id: int = 0
    text: str = ""
    mentions: tuple[int, ...] = ()
    forwarded: bool = False
    account_age_days: int | None = None
    is_admin: bool = False


@dataclass(slots=True)
class Verdict:
    """Outcome of evaluating one message."""

    level: str = "ok"
    reasons: tuple[str, ...] = ()
    strikes: int = 0
    dry_run: bool = True
    capped: bool = False

    def summary(self) -> str:
        return f"{self.level}:{','.join(self.reasons) or '-'}"


@runtime_checkable
class ModerationClient(Protocol):
    """The only Telegram surface the engine needs — inject a fake in tests."""

    async def delete_message(self, chat_id: int, message_id: int) -> Any: ...

    async def restrict_member(self, chat_id: int, user_id: int, until_date: int) -> Any: ...

    async def ban_member(self, chat_id: int, user_id: int) -> Any: ...

    async def send_message(self, chat_id: int, text: str) -> Any: ...
