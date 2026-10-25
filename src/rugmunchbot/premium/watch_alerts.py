#!/usr/bin/env python3
"""Portfolio-risk and watchlist alerts: quiet hours, a per-user cap, no repeats.

Three gates stand between a detected delta and a message, in this order —
quiet hours, per-user rate limit, dedupe. The order matters: a delta held by
quiet hours is retried later, but a delta dropped by dedupe is never retried,
so dedupe must only ever see a delta that is *about to be sent*.

The limiter is a sliding window, not a bucket, because "5 alerts per hour" is
what the product promises and a bucket lets 9 through around a refill.

Everything is injectable (clock, ledger) so the tests are pure and this module
never monkeypatches time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Alerts per hour, per user, by tier rank band (see ``gating.RANK``).
HOURLY_CAP: dict[str, int] = {"free": 5, "paid": 20}

#: Risk bands, high to low: (floor score, label, emoji).
BANDS: tuple[tuple[int, str, str], ...] = (
    (80, "critical", "🚨"),
    (60, "high", "⚠️"),
    (40, "elevated", "🟠"),
    (20, "watch", "🟡"),
    (0, "calm", "🟢"),
)


@dataclass(frozen=True, slots=True)
class QuietHours:
    """A UTC window that wraps midnight when ``end <= start``."""

    start: int = 22
    end: int = 6

    def covers(self, hour: int) -> bool:
        """True when ``hour`` falls inside the window."""
        if self.start == self.end:
            return False
        if self.start < self.end:
            return self.start <= hour < self.end
        return hour >= self.start or hour < self.end


@dataclass
class UserState:
    """Per-user alert state: the send log and what quiet hours hold back."""

    sent: list[float] = field(default_factory=list)
    held: dict[str, int] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)


class AlertGate:
    """Rate limit + quiet hours + dedupe, in that order of authority.

    ``now`` and ``clock`` are injectable: tests pass a fixed clock, production
    passes nothing.
    """

    def __init__(
        self,
        *,
        quiet: QuietHours | None = None,
        cap: int = HOURLY_CAP["paid"],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.quiet = quiet or QuietHours()
        self.cap = cap
        self._clock = clock
        self._users: dict[int, UserState] = {}

    def _state(self, user_id: int) -> UserState:
        return self._users.setdefault(user_id, UserState())

    def _recent(self, state: UserState, now: float) -> int:
        """Sends inside the trailing hour, pruning older entries in place."""
        state.sent = [at for at in state.sent if now - at < 3600]
        return len(state.sent)

    def admit(self, user_id: int, alert_key: str) -> str:
        """Decide one alert: ``send`` / ``hold`` / ``drop``.

        ``hold`` means quiet hours — the caller retries it later. ``drop``
        means it was already sent or the user is rate-limited; both are final
        for this key, so a retry loop cannot turn into a spam loop.
        """
        state = self._state(user_id)
        if alert_key in state.seen:
            return "drop"
        now = self._clock()
        if self.quiet.covers(time.gmtime(now).tm_hour):
            state.held[alert_key] = state.held.get(alert_key, 0) + 1
            return "hold"
        if self._recent(state, now) >= self.cap:
            return "drop"
        state.seen.add(alert_key)
        state.sent.append(now)
        return "send"

    def release(self, user_id: int) -> list[str]:
        """Keys held by quiet hours, in arrival order — the caller resends them."""
        state = self._state(user_id)
        keys = list(state.held)
        state.held.clear()
        return keys

    def held_count(self, user_id: int) -> int:
        """How many alerts quiet hours is sitting on."""
        return len(self._state(user_id).held)

    def reset(self, user_id: int) -> None:
        """Forget a user's state (opt-out, or a test pretending a new hour)."""
        self._users.pop(user_id, None)


def band(score: float) -> tuple[str, str]:
    """Label and emoji for a 0-100 risk score."""
    for floor, label, emoji in BANDS:
        if score >= floor:
            return label, emoji
    return BANDS[-1][1], BANDS[-1][2]


def portfolio_risk(positions: list[dict[str, float]]) -> dict[str, object]:
    """Weighted portfolio risk from ``[{score, weight}]``.

    Weight is normalised, so a user with two positions and one with twenty are
    scored on the same scale. An empty portfolio is calm, not an error.
    """
    total = sum(float(p.get("weight", 0)) for p in positions)
    if total <= 0:
        return {"score": 0.0, "label": band(0)[0], "emoji": band(0)[1], "worst": None}
    score = sum(float(p.get("score", 0)) * float(p.get("weight", 0)) for p in positions) / total
    worst = max(positions, key=lambda p: float(p.get("score", 0)))
    label, emoji = band(score)
    return {
        "score": round(score, 1),
        "label": label,
        "emoji": emoji,
        "worst": worst.get("symbol") or worst.get("address"),
    }


def render_alert(event: dict[str, object], *, tier: str) -> str:
    """The watchlist alert card — what moved, by how much, and why it matters."""
    score = float(event.get("score", 0) or 0)
    label, emoji = band(score)
    symbol = str(event.get("symbol") or event.get("address") or "token")
    chain = str(event.get("chain") or "")
    reason = str(event.get("reason") or "risk delta")
    hours = int(event.get("window_h", 1) or 1)
    held = ""
    if tier == "free":
        held = "\n<i>Free tier: 5 alerts/hour. /premium raises it to 20.</i>"
    return (
        f"{emoji} <b>{symbol}</b> ({chain}) — {label} {score:.0f}/100\n"
        f"{reason} · {hours}h window\n"
        f"<i>portfolio-weighted · quiet hours apply</i>{held}"
    )
