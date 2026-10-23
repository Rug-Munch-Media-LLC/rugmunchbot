"""Community settings models — token, amount presets and button config.

Split out of the recovered 648-line ``community/settings.py``. Constants and
input allowlists live here so the store, validation layer and the ``/setup``
admin commands all read from one source of truth.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

#: Chains a channel owner may point the chat at.
SUPPORTED_CHAINS: frozenset[str] = frozenset(
    {
        "ethereum",
        "bsc",
        "polygon",
        "arbitrum",
        "optimism",
        "base",
        "avalanche",
        "fantom",
        "solana",
        "aptos",
        "sui",
        "near",
        "ton",
        "tron",
    }
)

#: Chains whose addresses are 20-byte hex with a 0x prefix.
EVM_CHAINS: frozenset[str] = frozenset(
    {"ethereum", "bsc", "polygon", "arbitrum", "optimism", "base", "avalanche", "fantom"}
)

# Amount presets
MIN_AMOUNT = 0.0001
MAX_AMOUNT = 1_000_000.0
MAX_PRESETS = 10

# Button emoji/label
MAX_EMOJI_LEN = 4
MAX_LABEL_LEN = 16

# Redis layout
SETTINGS_KEY_PREFIX = "community:settings:"
AUDIT_KEY_PREFIX = "community:audit:"

_EVM_ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")
_SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_GENERIC_ADDRESS = re.compile(r"^[A-Za-z0-9:_.\-]{3,128}$")


class Chain(StrEnum):
    """Supported blockchain chains."""

    ETHEREUM = "ethereum"
    BSC = "bsc"
    POLYGON = "polygon"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"
    BASE = "base"
    AVALANCHE = "avalanche"
    FANTOM = "fantom"
    SOLANA = "solana"
    APTOS = "aptos"
    SUI = "sui"
    NEAR = "near"
    TON = "ton"
    TRON = "tron"


class TokenConfig(BaseModel):
    """The token a channel trades / talks about."""

    chain: Chain
    address: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=64)

    @field_validator("address")
    @classmethod
    def _normalise_address(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("address cannot be empty")
        return value.lower() if value.lower().startswith("0x") else value

    @field_validator("symbol")
    @classmethod
    def _normalise_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("symbol cannot be empty")
        if not value.replace("_", "").isalnum():
            raise ValueError("symbol must be alphanumeric or underscore")
        return value

    @field_validator("name")
    @classmethod
    def _normalise_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be empty")
        return value

    @model_validator(mode="after")
    def _check_address_format(self) -> TokenConfig:
        """Reject an address that cannot belong to the chosen chain."""
        chain = self.chain.value
        if chain in EVM_CHAINS and not _EVM_ADDRESS.match(self.address):
            raise ValueError(f"{chain} address must be 0x + 40 hex characters")
        if chain == "solana" and not _SOLANA_ADDRESS.match(self.address):
            raise ValueError("solana address must be 32-44 base58 characters")
        if (
            chain not in EVM_CHAINS
            and chain != "solana"
            and not _GENERIC_ADDRESS.match(self.address)
        ):
            raise ValueError("address contains unsupported characters")
        return self


class AmountPresets(BaseModel):
    """Buy/sell amount presets shown as inline buttons."""

    buy: list[float] = Field(default_factory=lambda: [0.1, 0.5, 1.0, 5.0, 10.0])
    sell: list[float] = Field(default_factory=lambda: [0.1, 0.25, 0.5, 0.75, 1.0])

    @field_validator("buy", "sell", mode="before")
    @classmethod
    def _parse_amounts(cls, value: Any) -> list[float]:
        if isinstance(value, str):
            return [_to_float(part) for part in value.split(",") if part.strip()]
        if isinstance(value, list | tuple):
            return [_to_float(item) for item in value]
        raise ValueError("amounts must be a comma-separated string or a list")

    @model_validator(mode="after")
    def _check_bounds(self) -> AmountPresets:
        for presets in (self.buy, self.sell):
            if len(presets) > MAX_PRESETS:
                raise ValueError(f"maximum {MAX_PRESETS} presets allowed")
            for amount in presets:
                if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                    raise ValueError(f"amounts must be between {MIN_AMOUNT} and {MAX_AMOUNT}")
        return self


def _to_float(raw: Any) -> float:
    """Parse one preset, raising a message an admin can act on."""
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not a number: {raw!r}") from exc


class ButtonConfig(BaseModel):
    """Emoji + label for one inline button."""

    emoji: str = Field(default="🟢", max_length=MAX_EMOJI_LEN)
    label: str = Field(default="Buy", max_length=MAX_LABEL_LEN)

    @field_validator("emoji")
    @classmethod
    def _check_emoji(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("emoji cannot be empty")
        if any(char.isspace() for char in value):
            raise ValueError("emoji cannot contain whitespace")
        return value

    @field_validator("label")
    @classmethod
    def _check_label(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("label cannot be empty")
        return value


class EmojiConfig(BaseModel):
    """Buy/sell button configuration."""

    buy: ButtonConfig = Field(default_factory=lambda: ButtonConfig(emoji="🟢", label="Buy"))
    sell: ButtonConfig = Field(default_factory=lambda: ButtonConfig(emoji="🔴", label="Sell"))
