"""Community settings — moderation, voting, features and the root document.

Every chat stores exactly one :class:`CommunitySettings` document in Redis,
keyed by ``chat_id``. Validation constraints live on the models so a bad value
can never reach Redis, whichever surface (``/setup`` or an internal caller)
wrote it.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from rugmunchbot.community.models import (
    AmountPresets,
    EmojiConfig,
    TokenConfig,
)

# Moderation limits
MAX_THRESHOLD = 1_000_000
MIN_THRESHOLD = 0
MAX_LINKS = 100
MAX_MENTIONS = 100

# Voting limits
MIN_VOTING_DURATION_HOURS = 1
MAX_VOTING_DURATION_DAYS = 30
MIN_PCT = 1
MAX_PCT = 100

#: Settable keys, used by the ``/setup`` dispatcher to reject typos.
MODERATION_FIELDS: frozenset[str] = frozenset(
    {
        "max_messages_per_minute",
        "max_links_per_message",
        "max_mentions_per_message",
        "delete_spam",
        "warn_on_violation",
        "auto_mute_minutes",
        "banned_words",
    }
)
VOTING_FIELDS: frozenset[str] = frozenset(
    {
        "enabled",
        "proposal_threshold_pct",
        "quorum_pct",
        "voting_duration_hours",
        "execute_on_pass",
        "admin_only_proposals",
    }
)
FEATURE_FIELDS: frozenset[str] = frozenset(
    {"trading", "moderation", "voting", "alerts", "analytics", "welcome", "anti_scam"}
)


class ModerationConfig(BaseModel):
    """Anti-spam thresholds and content filters."""

    max_messages_per_minute: int = Field(default=30, ge=MIN_THRESHOLD, le=MAX_THRESHOLD)
    max_links_per_message: int = Field(default=3, ge=MIN_THRESHOLD, le=MAX_LINKS)
    max_mentions_per_message: int = Field(default=5, ge=MIN_THRESHOLD, le=MAX_MENTIONS)
    delete_spam: bool = True
    warn_on_violation: bool = True
    auto_mute_minutes: int = Field(default=10, ge=MIN_THRESHOLD, le=MAX_THRESHOLD)
    banned_words: list[str] = Field(default_factory=list)

    @field_validator("banned_words", mode="before")
    @classmethod
    def _parse_words(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [word.strip().lower() for word in value.split(",") if word.strip()]
        if isinstance(value, list | tuple):
            return [str(word).strip().lower() for word in value if str(word).strip()]
        raise ValueError("banned_words must be a comma-separated string or a list")


class VotingConfig(BaseModel):
    """Governance / proposal settings."""

    enabled: bool = False
    proposal_threshold_pct: int = Field(default=5, ge=MIN_PCT, le=MAX_PCT)
    quorum_pct: int = Field(default=10, ge=MIN_PCT, le=MAX_PCT)
    voting_duration_hours: int = Field(
        default=24, ge=MIN_VOTING_DURATION_HOURS, le=MAX_VOTING_DURATION_DAYS * 24
    )
    execute_on_pass: bool = False
    admin_only_proposals: bool = True

    @model_validator(mode="after")
    def _check_thresholds(self) -> VotingConfig:
        if self.proposal_threshold_pct > self.quorum_pct:
            raise ValueError("proposal_threshold_pct cannot exceed quorum_pct")
        return self


class FeaturesConfig(BaseModel):
    """Per-chat feature flags."""

    trading: bool = True
    moderation: bool = True
    voting: bool = False
    alerts: bool = True
    analytics: bool = True
    welcome: bool = True
    anti_scam: bool = True


class CommunitySettings(BaseModel):
    """The complete per-chat settings document."""

    chat_id: int
    token: TokenConfig | None = None
    amounts: AmountPresets = Field(default_factory=AmountPresets)
    emojis: EmojiConfig = Field(default_factory=EmojiConfig)
    moderation: ModerationConfig = Field(default_factory=ModerationConfig)
    voting: VotingConfig = Field(default_factory=VotingConfig)
    admins: list[int] = Field(default_factory=list)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    updated_at: float = Field(default_factory=time.time)
    updated_by: int | None = None

    @field_validator("admins", mode="before")
    @classmethod
    def _parse_admins(cls, value: Any) -> list[int]:
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        if isinstance(value, list | tuple):
            return [int(item) for item in value]
        raise ValueError("admins must be a list of user ids or a comma-separated string")

    def to_storage(self) -> dict[str, Any]:
        """Serialise for Redis — ``chat_id`` is the key, so it is excluded."""
        return self.model_dump(mode="json", exclude={"chat_id"})

    @classmethod
    def from_storage(cls, chat_id: int, data: dict[str, Any]) -> CommunitySettings:
        """Rehydrate a stored document (chain strings normalise back to enums)."""
        return cls.model_validate({**data, "chat_id": chat_id})
