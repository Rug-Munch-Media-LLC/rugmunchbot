"""Paid watch UX: quota refusal, chain inference, and what the user is told.

The handlers are driven with a fake update/context and a patched transport, so
these assertions are about the message a Telegram user actually reads and the
backend calls the bot makes — not about Telegram internals.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rugmunchbot import db
from rugmunchbot.alert_worker import _format_alert
from rugmunchbot.handlers.premium_watch import _api, _commands, _limits

SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.documents: list[dict[str, Any]] = []
        self.edits: list[str] = []
        self.deleted = False

    async def reply_text(self, text: str, **_: Any) -> FakeMessage:
        self.texts.append(text)
        return self

    async def reply_document(self, document: Any, **kwargs: Any) -> None:
        self.documents.append({"document": document, **kwargs})

    async def edit_text(self, text: str, **_: Any) -> None:
        self.edits.append(text)

    async def delete(self) -> None:
        self.deleted = True


def _ctx(*args: str) -> Any:
    return SimpleNamespace(args=list(args))


def _update(message: FakeMessage | None = None) -> Any:
    return SimpleNamespace(
        effective_message=message or FakeMessage(),
        effective_user=SimpleNamespace(id=4242, username="tester", first_name="T"),
    )


def _run(coro: Any) -> None:
    asyncio.run(coro)


def test_free_tier_gets_two_and_paid_gets_twenty_five() -> None:
    assert _limits.watch_limit("free") == 2
    assert _limits.watch_limit("pro") == 25
    assert _limits.watch_limit("anything-unknown") == 2
    refusal = _limits.limit_refusal(2, 2)
    assert "2/2" in refusal and "25" in refusal and "/pricing" in refusal


def test_watch_refuses_past_the_free_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []

    async def fake_list(chat_id: str) -> list[dict]:
        return [{"token": "a" * 32, "chain": "solana"}, {"token": "b" * 32, "chain": "solana"}]

    async def fake_watch(*args: Any) -> dict:
        calls.append(args)
        return {"watchers_n": 1}

    monkeypatch.setattr(_api, "list_watches", fake_list)
    monkeypatch.setattr(_api, "watch_token", fake_watch)
    monkeypatch.setattr(db, "get_user_tier", lambda _uid: "free")
    update = _update()

    _run(_commands.cmd_watch(update, _ctx(SOL)))

    assert calls == [], "a full watchlist must not reach the backend"
    assert "Watchlist full" in update.effective_message.texts[0]


def test_watch_infers_chain_and_confirms_a_resubscribe(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_list(chat_id: str) -> list[dict]:
        return [{"token": SOL, "chain": "solana"}]

    async def fake_watch(token: str, chain: str, chat_id: str) -> dict:
        seen.update(token=token, chain=chain, chat_id=chat_id)
        return {"watchers_n": 3}

    monkeypatch.setattr(_api, "list_watches", fake_list)
    monkeypatch.setattr(_api, "watch_token", fake_watch)
    monkeypatch.setattr(db, "get_user_tier", lambda _uid: "free")
    update = _update()

    _run(_commands.cmd_watch(update, _ctx(SOL)))

    assert seen == {"token": SOL, "chain": "solana", "chat_id": "4242"}
    text = update.effective_message.texts[0]
    assert "3 watching" in text and "authority_changed" in text and "/unwatch" in text


def test_watch_rejects_an_address_that_is_neither_solana_nor_evm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*args: Any) -> Any:
        raise AssertionError("no backend call for an unusable address")

    monkeypatch.setattr(_api, "watch_token", boom)
    update = _update()

    _run(_commands.cmd_watch(update, _ctx("not-an-address")))

    assert "Usage: /watch" in update.effective_message.texts[0]


def test_unwatch_reports_absence_without_pretending(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_unwatch(token: str, chain: str, chat_id: str) -> dict:
        return {"removed": False, "watchers_n": 0}

    monkeypatch.setattr(_api, "unwatch_token", fake_unwatch)
    update = _update()

    _run(_commands.cmd_unwatch(update, _ctx(SOL)))

    assert "Not watching" in update.effective_message.texts[0]


def test_watchlist_shows_quota_and_per_row_unwatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_list(chat_id: str) -> list[dict]:
        return [{"token": SOL, "chain": "solana", "watchers_n": 4}]

    monkeypatch.setattr(_api, "list_watches", fake_list)
    monkeypatch.setattr(db, "get_user_tier", lambda _uid: "pro")
    update = _update()

    _run(_commands.cmd_watchlist(update, _ctx()))

    text = update.effective_message.texts[0]
    assert "1/25 (pro)" in text and "4 watching" in text and f"/unwatch <code>{SOL}</code>" in text


def test_export_sends_the_canonical_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"schema_version":"premium.scan.v1","token":"x"}'

    async def fake_export(token: str, chain: str) -> bytes:
        return body

    monkeypatch.setattr(_api, "export_scan", fake_export)
    update = _update()

    _run(_commands.cmd_export(update, _ctx(SOL)))

    document = update.effective_message.documents[0]
    assert document["document"].input_file_content == body
    assert "premium.scan.v1" in document["caption"]
    assert update.effective_message.deleted is True


def test_alert_card_carries_the_delta_numbers_and_a_deep_link() -> None:
    """The queue item the backend pushes renders as a card with both facts."""
    item = {
        "type": "token.monitor_change",
        "payload": {
            "token_address": SOL,
            "token_symbol": "USDC",
            "chain": "solana",
            "changes": [
                "Liquidity: $100,000 -> $42,000",
                "Top holder cashed out: BagH...1111 held 9.50%, now gone",
            ],
        },
    }

    text = _format_alert(item)

    assert "Liquidity: $100,000 -> $42,000" in text
    assert "9.50%" in text
    assert f"/scan <code>{SOL.lower()}</code>" in text
