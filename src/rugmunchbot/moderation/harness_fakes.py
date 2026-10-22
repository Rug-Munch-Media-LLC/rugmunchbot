"""Telegram doubles for the dry harness: no network, no real job queue."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from rugmunchbot.moderation.models import MsgContext
from rugmunchbot.moderation.policy import ChatPolicy

CHAT_ID = -1001234567890
MATH_CHAT_ID = -1001234567891


class FakeBot:
    """Records every attribute call; nothing ever leaves the process."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        async def _record(*args: Any, **kwargs: Any) -> None:
            self.calls.append(name)

        return _record


@dataclass
class FakeJob:
    callback: Any
    when: float
    name: str


class FakeQueue:
    """Collects scheduled jobs instead of running them on a clock."""

    def __init__(self) -> None:
        self.jobs: list[FakeJob] = []

    def run_once(self, callback: Any, when: float, name: str = "") -> None:
        self.jobs.append(FakeJob(callback=callback, when=when, name=name))


class FakeQuery:
    def __init__(self, data: str, user: FakeUser) -> None:
        self.data = data
        self.from_user = user
        self.answers: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        self.answers.append(text)


@dataclass
class FakeUser:
    id: int
    username: str = "member"
    full_name: str = "Member"
    first_name: str = "Member"
    is_bot: bool = False


@dataclass
class FakeMessage:
    new_chat_members: list[FakeUser] = field(default_factory=list)
    text: str = ""
    caption: str = ""
    entities: tuple = ()
    message_id: int = 1
    photo: Any = None
    video: Any = None
    animation: Any = None
    document: Any = None

    def parse_entity(self, entity: Any) -> str:  # pragma: no cover — no entities in harness
        return ""


@dataclass
class FakeChat:
    id: int
    title: str = "RugMunch"
    username: str = "rugmunch"
    type: str = "supergroup"


class FakeUpdate:
    def __init__(
        self,
        *,
        message: FakeMessage | None = None,
        chat: FakeChat | None = None,
        user: FakeUser | None = None,
        callback_query: FakeQuery | None = None,
    ) -> None:
        self.message = message
        self.effective_message = message
        self.effective_chat = chat
        self.effective_user = user
        self.edited_message = None
        self.callback_query = callback_query


def rig_context(bot: FakeBot, queue: FakeQueue) -> SimpleNamespace:
    return SimpleNamespace(bot=bot, job_queue=queue)


def policy(**overrides: Any) -> ChatPolicy:
    return ChatPolicy.from_dict(CHAT_ID, overrides)


def ctx(text: str = "", **kw: Any) -> MsgContext:
    base: dict[str, Any] = {
        "chat_id": CHAT_ID,
        "chat_title": "RugMunch",
        "user_id": 7,
        "username": "member",
        "display_name": "Member",
        "text": text,
    }
    base.update(kw)
    return MsgContext(**base)
