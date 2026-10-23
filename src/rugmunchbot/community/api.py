"""Stable read API for other modules (trading, moderation, voting, UI).

Callers depend on these typed getters rather than reaching into Redis or the
settings document, so the storage shape can change without touching every
consumer. ``client`` is the same optional DI seam the rest of the package uses.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.community.store import load_settings


async def get_token_config(chat_id: int, client: Any = None) -> dict[str, str] | None:
    """``{chain, address, symbol, name}`` or ``None`` when unset."""
    settings = await load_settings(chat_id, client)
    if settings.token is None:
        return None
    return {
        "chain": settings.token.chain.value,
        "address": settings.token.address,
        "symbol": settings.token.symbol,
        "name": settings.token.name,
    }


async def get_amount_presets(chat_id: int, client: Any = None) -> dict[str, list[float]]:
    """Buy/sell preset amounts."""
    settings = await load_settings(chat_id, client)
    return {"buy": list(settings.amounts.buy), "sell": list(settings.amounts.sell)}


async def get_button_config(chat_id: int, client: Any = None) -> dict[str, dict[str, str]]:
    """Buy/sell button emoji + label."""
    settings = await load_settings(chat_id, client)
    return {
        "buy": {"emoji": settings.emojis.buy.emoji, "label": settings.emojis.buy.label},
        "sell": {"emoji": settings.emojis.sell.emoji, "label": settings.emojis.sell.label},
    }


async def get_moderation_config(chat_id: int, client: Any = None) -> dict[str, Any]:
    """Moderation thresholds and filters."""
    settings = await load_settings(chat_id, client)
    return settings.moderation.model_dump()


async def get_voting_config(chat_id: int, client: Any = None) -> dict[str, Any]:
    """Governance settings."""
    settings = await load_settings(chat_id, client)
    return settings.voting.model_dump()


async def get_features_config(chat_id: int, client: Any = None) -> dict[str, bool]:
    """Feature flags."""
    settings = await load_settings(chat_id, client)
    return settings.features.model_dump()


async def get_admin_ids(chat_id: int, client: Any = None) -> list[int]:
    """Explicit admin user ids (Telegram admins are resolved separately)."""
    settings = await load_settings(chat_id, client)
    return list(settings.admins)


async def is_feature_enabled(chat_id: int, feature: str, client: Any = None) -> bool:
    """``False`` for an unknown flag — features are opt-in, never implied."""
    settings = await load_settings(chat_id, client)
    return bool(getattr(settings.features, feature, False))
