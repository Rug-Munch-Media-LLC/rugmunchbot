#!/usr/bin/env python3
"""Weekly usage meter and the reset clock behind ``/premium``.

The meter answers three questions in one card: how much is left, when it
resets, and what the next tier adds. Weekly allotments reset Monday 00:00 UTC
(``db.tiers._current_week_start`` is the single source of that rule) — the
reset *clock* here is derived from the same weekday arithmetic so the card and
the counter can never disagree about which week is being shown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rugmunchbot.config import TIERS

if TYPE_CHECKING:
    from collections.abc import Callable

#: The meter rows, in display order: (usage key, tier key, label, unit).
ROWS: tuple[tuple[str, str, str, str], ...] = (
    ("scans", "scans_per_week", "Scans", "scan"),
    ("ai_msgs", "ai_msgs_per_week", "AI questions", "question"),
)

#: Rank order of paid tiers when suggesting the next step up.
LADDER: tuple[str, ...] = ("free", "scout", "hunter", "pro")


@dataclass(frozen=True, slots=True)
class Usage:
    """One user's meter at one instant."""

    tier: str
    used: dict[str, int]
    limits: dict[str, int]
    bonus: dict[str, int]
    reset_at: datetime

    @property
    def reset_in_seconds(self) -> int:
        """Seconds until the weekly allotment refills (never negative)."""
        return max(0, int((self.reset_at - datetime.now(UTC)).total_seconds()))

    def remaining(self, key: str) -> int:
        """Weekly headroom left for one row, floored at zero."""
        return max(0, self.limits.get(key, 0) - self.used.get(key, 0))


def next_reset(now: datetime | None = None) -> datetime:
    """The upcoming Monday 00:00 UTC — the instant the weekly counter clears."""
    current = now or datetime.now(UTC)
    days = (7 - current.weekday()) % 7
    candidate = (current + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return candidate if candidate > current else candidate + timedelta(days=7)


def snapshot(
    tier: str,
    used: dict[str, int],
    bonus: dict[str, int] | None = None,
    *,
    now: datetime | None = None,
) -> Usage:
    """Build the meter from a tier name and the counters already read."""
    definition = TIERS.get(str(tier or "free").strip().lower(), TIERS["free"])
    limits = {key: int(definition.get(tier_key, 0)) for key, tier_key, _, _ in ROWS}
    return Usage(
        tier=str(tier or "free"),
        used={key: int(used.get(key, 0)) for key, _, _, _ in ROWS},
        limits=limits,
        bonus={key: int((bonus or {}).get(key, 0)) for key, _, _, _ in ROWS},
        reset_at=next_reset(now),
    )


def _bar(fraction: float, width: int = 10) -> str:
    """A ten-cell progress bar; a fully used row still shows one cell."""
    filled = min(width, max(1 if fraction > 0 else 0, round(fraction * width)))
    return "▓" * filled + "░" * (width - filled)


def next_tier(tier: str) -> str | None:
    """The next rung, or ``None`` when already at the top of the ladder."""
    name = str(tier or "free").strip().lower()
    if name not in LADDER:
        return "scout"
    index = LADDER.index(name)
    return LADDER[index + 1] if index + 1 < len(LADDER) else None


def render(usage: Usage) -> str:
    """The ``/premium`` card: quota, reset time, and the upgrade path."""
    definition = TIERS.get(usage.tier, TIERS["free"])
    lines = [
        f"{definition['emoji']} <b>{definition['name']}</b> — weekly quota",
        "",
    ]
    for key, _, label, unit in ROWS:
        limit = usage.limits.get(key, 0)
        used = usage.used.get(key, 0)
        fraction = (used / limit) if limit else 1.0
        bonus = usage.bonus.get(key, 0)
        extra = f"  <i>+{bonus} top-up</i>" if bonus else ""
        lines.append(f"{label}: <b>{used}/{limit}</b> {_bar(fraction)}{extra}")
        if usage.remaining(key) == 0 and bonus == 0:
            lines.append(f"  ⛔ out of {unit}s until reset")
    hours, minutes = divmod(usage.reset_in_seconds // 60, 60)
    lines += ["", f"♻️ Resets in <b>{hours}h {minutes}m</b> (Mon 00:00 UTC)"]
    upgrade = next_tier(usage.tier)
    if upgrade is None:
        lines += ["", "👑 You are on the top tier — thank you."]
    else:
        nxt = TIERS.get(upgrade, TIERS["scout"])
        lines += [
            "",
            f"⬆️ <b>{nxt['name']}</b> — {nxt['scans_per_week']} scans/week, "
            f"{nxt['ai_msgs_per_week']} AI questions/week.",
            "Run /pricing to upgrade, or /help for what is included.",
        ]
    return "\n".join(lines)


def read(user_id: int, *, read_tier: Callable[[int], str] | None = None) -> Usage:
    """Read the live meter for a user (one DB round trip per source)."""
    from rugmunchbot import db

    tier = (read_tier or db.get_user_tier)(user_id)
    scans, ai = db.get_weekly_usage(user_id)
    return snapshot(tier, {"scans": scans, "ai_msgs": ai})
