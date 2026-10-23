"""Shared offline fakes for the /buybot tests (no network, no Redis, no keys)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from rugmunchbot.buybot import handlers as h
from rugmunchbot.buybot.execution import BuyExecutor
from rugmunchbot.buybot.ledger import CardLedger
from rugmunchbot.buybot.state import STATE_KEY, BuybotState
from rugmunchbot.community.store import COMMUNITY_CLIENT_KEY

MINT = "So11111111111111111111111111111111111111112"
ADMIN = 424242
CHAT = -1009
TOKEN_DOC = {
    "chat_id": CHAT,
    "enabled": True,
    "token": {"chain": "solana", "address": MINT, "symbol": "BONK", "name": "Bonk"},
}


class FakeBot:
    def __init__(self, admins: tuple[int, ...] = (ADMIN,)) -> None:
        self._admins = [SimpleNamespace(user=SimpleNamespace(id=i)) for i in admins]

    async def get_chat_administrators(self, chat_id: int):
        return self._admins


class FakeRedis:
    def __init__(self, document: dict | None = None) -> None:
        self.document = document

    async def get(self, key: str):
        return json.dumps(self.document) if self.document is not None else None

    async def set(self, key: str, value: str) -> None:
        self.document = json.loads(value)


class FakeSwap:
    def __init__(self, tx: str = "0xfeedface1234") -> None:
        self.tx = tx
        self.calls = 0

    async def buy(self, request) -> str:
        self.calls += 1
        return self.tx


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.keyboards: list[object] = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append(text)
        self.keyboards.append(kwargs.get("reply_markup"))
        return SimpleNamespace(message_id=10)


class FakeQuery:
    def __init__(self, data: str, user_id: int = 7) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(chat=SimpleNamespace(id=CHAT))
        self.answers: list[str] = []

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        self.answers.append(text)


def update(user_id: int = ADMIN, query=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name="Ann"),
        effective_chat=SimpleNamespace(id=CHAT),
        effective_message=FakeMessage(),
        callback_query=query,
    )


def context(bot=None, redis=None, state=None, args: list[str] | None = None):
    data = {COMMUNITY_CLIENT_KEY: redis or FakeRedis()}
    if state is not None:
        data[STATE_KEY] = state
    return SimpleNamespace(
        bot=bot or FakeBot(),
        application=SimpleNamespace(bot_data=data),
        args=list(args or []),
    )


def state(swap=None) -> BuybotState:
    return BuybotState(ledger=CardLedger(), executor=BuyExecutor(swap), market=None)


def registered() -> tuple[dict[str, object], list[object]]:
    found: list[object] = []
    h.register_buybot(SimpleNamespace(add_handler=lambda handler, group=0: found.append(handler)))
    commands = {next(iter(i.commands)): i.callback for i in found if hasattr(i, "commands")}
    return commands, [i for i in found if not hasattr(i, "commands")]


def call(name: str, update_obj, context_obj) -> None:
    asyncio.run(registered()[0][name](update_obj, context_obj))


def tap(query: FakeQuery, context_obj) -> None:
    update_obj = SimpleNamespace(callback_query=query)
    asyncio.run(registered()[1][0].callback(update=update_obj, context=context_obj))
