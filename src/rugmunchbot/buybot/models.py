"""Buy-popup configuration for one chat (``/buybot``).

One source of truth: the token, the amount presets and the buy button come
from :mod:`rugmunchbot.community.models` — the same models ``/setup`` writes.
This module adds only what the popup itself needs (which rows to print, which
buttons to draw, how hard to rate-limit) and validates everything an
administrator can type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from rugmunchbot.community.models import AmountPresets, Chain, EmojiConfig, TokenConfig

if TYPE_CHECKING:
    from rugmunchbot.community.config_models import CommunitySettings

__all__ = [
    "CARD_BUTTONS",
    "CARD_FIELDS",
    "CONFIG_KEY_PREFIX",
    "DEFAULT_FIELDS",
    "MAX_CARDS_PER_MIN",
    "BuybotConfig",
]

#: Card rows, in the fixed order they are rendered (a subset is configurable).
CARD_FIELDS: tuple[str, ...] = ("amount", "usd", "price", "mcap", "liquidity", "risk")
#: Inline buttons: one per amount preset (always) plus these action rows.
CARD_BUTTONS: tuple[str, ...] = ("chart", "scan", "holders")
DEFAULT_FIELDS: tuple[str, ...] = CARD_FIELDS
CONFIG_KEY_PREFIX = "buybot:config:"
MAX_USD = 1_000_000_000.0
MAX_CARDS_PER_MIN = 30


class BuybotConfig(BaseModel):
    """Per-chat buy-popup config. Disabled and token-less by default."""

    chat_id: int = 0
    enabled: bool = False
    token: TokenConfig | None = None
    amounts: AmountPresets = Field(default_factory=AmountPresets)
    emojis: EmojiConfig = Field(default_factory=EmojiConfig)
    min_buy_usd: float = Field(default=0.0, ge=0.0, le=MAX_USD)
    fields: tuple[str, ...] = DEFAULT_FIELDS
    buttons: tuple[str, ...] = CARD_BUTTONS
    dedupe_window_s: int = Field(default=20, ge=0, le=3600)
    max_cards_per_min: int = Field(default=10, ge=1, le=MAX_CARDS_PER_MIN)

    @field_validator("fields", "buttons", mode="before")
    @classmethod
    def _ordered_subset(cls, value: Any, info: Any) -> tuple[str, ...]:
        """Accept any order, reject unknown names, always store canonical order."""
        name = info.field_name
        allowed = CARD_FIELDS if name == "fields" else CARD_BUTTONS
        if isinstance(value, str):
            value = value.split(",")
        wanted = {str(item).strip().lower() for item in value if str(item).strip()}
        unknown = wanted.difference(allowed)
        if unknown:
            raise ValueError(f"unknown {name}: {', '.join(sorted(unknown))}")
        if not wanted:
            raise ValueError(f"pick at least one of: {', '.join(allowed)}")
        return tuple(item for item in allowed if item in wanted)

    @model_validator(mode="after")
    def _enabled_needs_token(self) -> BuybotConfig:
        if self.enabled and self.token is None:
            raise ValueError("set a token before enabling the popup")
        return self

    @property
    def chain(self) -> Chain:
        """Chain of the configured token (``solana`` until a token is set)."""
        return self.token.chain if self.token is not None else Chain.SOLANA

    @property
    def presets(self) -> list[float]:
        """Buy amounts offered as buttons (community ``amounts.buy``)."""
        return list(self.amounts.buy)

    @classmethod
    def from_community(cls, chat_id: int, settings: CommunitySettings) -> BuybotConfig:
        """Seed a popup config from the chat's ``/setup`` document."""
        return cls(
            chat_id=chat_id,
            token=settings.token,
            amounts=settings.amounts,
            emojis=settings.emojis,
        )
