"""Identity regression gate: product attribution is Rug Munch Intelligence."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from rugmunchbot.bot._profile import setup_bot_profile
from rugmunchbot.config import BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION
from rugmunchbot.config._branding import BOT_NAME
from rugmunchbot.formatters._base._texts import about_text, welcome_card_text

BRAND = "Rug Munch Intelligence"
SURFACES = (about_text(), welcome_card_text())


class _StubBot:
    """Records profile calls; any other bot coroutine is a no-op."""

    username = "RugMunchBot"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def get_me(self) -> None:
        return None

    async def set_my_name(self, name: str) -> None:
        self.calls.append(("set_my_name", name))

    async def set_my_description(self, description: str) -> None:
        self.calls.append(("set_my_description", description))

    async def set_my_short_description(self, short_description: str) -> None:
        self.calls.append(("set_my_short_description", short_description))

    async def set_my_commands(self, commands: object) -> None:
        return None

    def __getattr__(self, _name: str):
        async def _noop(*_a: object, **_kw: object) -> None:
            return None

        return _noop


def test_registration_strings_carry_brand() -> None:
    """BotFather name, description and short description are the brand."""
    assert BOT_NAME == BRAND
    assert BOT_DESCRIPTION.startswith("🛡️ " + BRAND)
    assert BOT_SHORT_DESCRIPTION


def test_user_copy_has_no_personal_attribution() -> None:
    """No personal handle or first name ships in about/welcome copy."""
    for surface in SURFACES:
        assert "@cryptorugmunch" not in surface
        assert "marcus" not in surface.lower()
        assert BRAND in surface


def test_startup_registers_name_and_descriptions() -> None:
    """setup_bot_profile pushes the brand to Telegram at startup."""
    bot = _StubBot()
    with (
        patch("rugmunchbot.bot._profile.register_surface_commands"),
        patch("rugmunchbot.bot._profile.check_menu_coverage"),
        patch("rugmunchbot.bot._profile.assert_menu_coverage"),
    ):
        asyncio.run(setup_bot_profile(SimpleNamespace(bot=bot)))

    assert ("set_my_name", BRAND) in bot.calls
    assert ("set_my_description", BOT_DESCRIPTION) in bot.calls
    assert ("set_my_short_description", BOT_SHORT_DESCRIPTION) in bot.calls
