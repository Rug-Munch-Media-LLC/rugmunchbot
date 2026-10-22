"""Moderation value objects — pure data, no I/O, no Telegram imports."""

from __future__ import annotations

from dataclasses import dataclass, field

# Every action a rule may return. "allow" is never returned by a rule; the
# absence of a rule hit IS allow.
ACTIONS = ("warn", "delete", "mute", "ban", "alert")

# Higher wins when two rules fire on the same message.
_SEVERITY = {"warn": 1, "delete": 2, "alert": 3, "mute": 4, "ban": 5}


@dataclass(slots=True)
class MsgContext:
    """Everything a rule needs about one update, already normalised."""

    chat_id: int
    chat_title: str
    user_id: int
    username: str
    display_name: str
    text: str = ""
    urls: tuple[str, ...] = ()
    media: str | None = None  # "photo" | "video" | "animation" | "document" | None
    is_new_member: bool = False
    is_admin: bool = False
    message_id: int | None = None


@dataclass(slots=True, frozen=True)
class AddressVerdict:
    """Result of scanning one address posted in chat."""

    address: str
    chain: str = "unknown"
    risk_level: str = "unknown"
    safety_score: int | None = None
    verdict: str = "unknown"  # ok | caution | avoid | unknown
    reason: str = ""

    @property
    def hazardous(self) -> bool:
        return self.verdict in {"avoid", "caution"}


@dataclass(slots=True)
class Action:
    """A moderation decision: what to do, why, and what to show the user."""

    rule: str
    action: str
    reason: str
    matched: str = ""
    reply: str = ""
    delete: bool = False
    mute_minutes: int = 0
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def severity(self) -> int:
        return _SEVERITY.get(self.action, 0)

    @property
    def blocks_pipeline(self) -> bool:
        """True when the message must not reach the AI/chat pipeline."""
        return self.action in {"delete", "mute", "ban"}


def worst(*actions: Action | None) -> Action | None:
    """Return the most severe non-None action."""
    candidates = [a for a in actions if a is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.severity)


__all__ = ["ACTIONS", "Action", "AddressVerdict", "MsgContext", "worst"]
