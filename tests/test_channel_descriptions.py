"""Tests for channel housekeeping (bot/_channels_setup).

Covers: idempotent description setting (skip when unchanged, set when
different, fail-soft per channel) and the admin verification (warn-only,
returns not-admin roles).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rugmunchbot.bot import _channels_setup as cs


@pytest.fixture(autouse=True)
def _fake_channel_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic role→chat-id resolution (no gopass/env in tests)."""
    monkeypatch.setattr(cs, "_channel_id", lambda role: role)


def _app() -> MagicMock:
    app = MagicMock()
    bot = MagicMock()
    bot.id = 42
    bot.get_chat = AsyncMock()
    bot.set_chat_description = AsyncMock()
    bot.get_chat_member = AsyncMock()
    app.bot = bot
    return app


class _Chat:
    def __init__(self, description: str = "") -> None:
        self.description = description


class TestDescriptions:
    @pytest.mark.asyncio
    async def test_sets_all_descriptions_when_empty(self) -> None:
        app = _app()
        app.bot.get_chat.return_value = _Chat(description="")
        updated = await _channels_setup_set(app)
        assert updated == len(_channels_setup_descriptions())
        assert app.bot.set_chat_description.await_count == updated

    @pytest.mark.asyncio
    async def test_idempotent_skips_unchanged(self) -> None:
        app = _app()
        descs = _channels_setup_descriptions()
        app.bot.get_chat.side_effect = lambda chat_id: _Chat(description=descs[chat_id])
        updated = await _channels_setup_set(app)
        assert updated == 0
        app.bot.set_chat_description.assert_not_called()

    @pytest.mark.asyncio
    async def test_updates_only_stale_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app = _app()
        descs = _channels_setup_descriptions()
        first_role = next(iter(descs))
        monkeypatch.setattr(cs, "_channel_id", lambda role: role)
        app.bot.get_chat.side_effect = lambda chat_id: _Chat(
            description="" if chat_id == first_role else descs[chat_id]
        )
        updated = await _channels_setup_set(app)
        assert updated == 1

    @pytest.mark.asyncio
    async def test_fail_soft_on_telegram_error(self) -> None:
        app = _app()
        app.bot.get_chat.side_effect = RuntimeError("api down")
        updated = await _channels_setup_set(app)
        assert updated == 0  # never raises


class TestAdminCheck:
    @pytest.mark.asyncio
    async def test_all_admin_returns_empty(self) -> None:
        app = _app()
        member = MagicMock()
        member.status = "administrator"
        app.bot.get_chat_member.return_value = member
        assert await _channels_setup_verify(app) == []

    @pytest.mark.asyncio
    async def test_non_admin_roles_reported(self) -> None:
        app = _app()
        member = MagicMock()
        member.status = "member"
        app.bot.get_chat_member.return_value = member
        not_admin = await _channels_setup_verify(app)
        assert len(not_admin) == len(_channels_setup_descriptions())

    @pytest.mark.asyncio
    async def test_creator_counts_as_admin(self) -> None:
        app = _app()
        member = MagicMock()
        member.status = "creator"
        app.bot.get_chat_member.return_value = member
        assert await _channels_setup_verify(app) == []

    @pytest.mark.asyncio
    async def test_api_error_counts_as_not_admin(self) -> None:
        app = _app()
        app.bot.get_chat_member.side_effect = RuntimeError("down")
        not_admin = await _channels_setup_verify(app)
        assert not_admin  # never raises, reports the failure


# helpers (import lazily so module-level config stays lazy)
def _channels_setup_set(app: Any) -> Any:
    from rugmunchbot.bot._channels_setup import set_channel_descriptions

    return set_channel_descriptions(app)


def _channels_setup_verify(app: Any) -> Any:
    from rugmunchbot.bot._channels_setup import verify_channel_admin

    return verify_channel_admin(app)


def _channels_setup_descriptions() -> dict[str, str]:
    from rugmunchbot.bot._channels_setup import CHANNEL_DESCRIPTIONS

    return CHANNEL_DESCRIPTIONS
