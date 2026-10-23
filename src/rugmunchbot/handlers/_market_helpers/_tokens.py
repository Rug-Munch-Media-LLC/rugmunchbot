"""Token-level primitives: addresses, ages, time windows and ranking bonuses.

Pure functions only — a token dict in, a scalar out. Split out of
``handlers/_market_helpers.py`` (2026-10-23).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


def _normalize_address(addr: str) -> str:
    """Normalize address for deduplication."""
    return addr.lower().strip()


def _token_age_hours(token: dict[str, Any]) -> float | None:
    """Age of a token in hours from ``created_at`` (ms epoch), None when absent."""
    created = token.get("created_at") or token.get("pairCreatedAt")
    if not created:
        return None
    try:
        created_ms = float(created)
    except (TypeError, ValueError):
        return None
    if created_ms <= 0:
        return None
    return max((datetime.now(UTC).timestamp() * 1000 - created_ms) / 3_600_000.0, 0.0)


# Age badge thresholds (hours)
AGE_BADGES = [
    (24, "🆕"),  # < 24h
    (168, "🌱"),  # < 7d
    (720, "🌿"),  # < 30d
    (float("inf"), "🌳"),  # > 30d
]


def _age_badge(created_at_ms: int | None) -> str:
    """Return age badge based on token creation time."""
    if not created_at_ms:
        return "❓"
    try:
        created = datetime.fromtimestamp(created_at_ms / 1000, tz=UTC)
        age_hours = (datetime.now(UTC) - created).total_seconds() / 3600
        for threshold, badge in AGE_BADGES:
            if age_hours < threshold:
                return badge
    except (ValueError, TypeError, OverflowError, OSError) as e:
        logger.debug("age badge compute failed: %s", e)
    return "❓"


def _freshness_bonus(token: dict[str, Any]) -> int:
    """Fresh-launch bonus: freshly deployed tokens rank above old infra coins.

    +2 for < 24h old (pump.fun / new launches), +1 for < 7d. Unknown age
    contributes nothing (no invented freshness).
    """
    age_h = _token_age_hours(token)
    if age_h is None:
        return 0
    if age_h < 24:
        return 2
    if age_h < 168:
        return 1
    return 0


# ── Trending time windows ──────────────────────────────────────────────
TRENDING_WINDOWS = ("1h", "24h", "7d")
_WINDOW_FIELD = {"1h": "change_1h", "24h": "change_24h", "7d": "change_7d"}


def _window_change(token: dict[str, Any], window: str) -> float:
    """Change % for the requested window, falling back to 24h when absent."""
    key = _WINDOW_FIELD.get(window, "change_24h")
    val = token.get(key)
    if val is None:
        val = token.get("change_24h", 0)
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def _mover_bonus(token: dict[str, Any]) -> int:
    """Mover bonus: biggest |24h %| movers (up AND down) surface first.

    +2 for |change| >= 100%, +1 for >= 25%. Negative (rug/dump) changes get
    the same bonus as positive ones — degens want the red action too.
    """
    chg = abs(_window_change(token, "24h"))
    if chg >= 100:
        return 2
    if chg >= 25:
        return 1
    return 0
