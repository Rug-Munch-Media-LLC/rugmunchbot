# VENDORED 2026-08-23 from rmi-backend/app/domains/telegram/headless_models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Headless Telegram Client — Data Models
=======================================
Shared dataclasses and enums for the headless Telegram client.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GroupType(Enum):
    """Types of Telegram groups we manage."""

    CREATOR_COIN = "creator_coin"
    COMMUNITY = "community"
    ALERTS = "alerts"
    PREMIUM = "premium"


@dataclass
class GroupConfig:
    """Configuration for a managed Telegram group."""

    group_type: GroupType
    title: str
    description: str | None = None
    username: str | None = None
    is_public: bool = False
    slow_mode_seconds: int = 0
    join_by_request: bool = False
    creator_user_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupInfo:
    """Runtime information about a managed group."""

    chat_id: int
    title: str
    username: str | None
    invite_link: str | None
    member_count: int
    is_public: bool
    group_type: GroupType
    created_at: float = field(default_factory=time.time)
