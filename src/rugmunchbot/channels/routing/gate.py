"""One gate that every channel post passes through.

``decide(channel, category, severity)`` answers two questions at once:

1. **Policy** — may this channel carry this content at all? (table lookup,
   severity floor, category allowlist, archived / manual-only channels,
   cross-channel redirects).
2. **Rate** — has this channel already posted ``max_per_hour`` items in the
   trailing hour? (in-process sliding window; the bot owns every job in one
   process, so no cross-process coordination is needed).

Dry-run is the DEFAULT: ``RMI_CHANNEL_DRY_RUN`` unset means "compute the
decision, log it, do not send". Set ``RMI_CHANNEL_DRY_RUN=0`` (or the
per-channel ``RMI_CHANNEL_<ROLE>_DRY_RUN=0``) to go live.

Every decision carries a human-readable ``reason`` so an audit can say why a
post was suppressed: ``archived``, ``manual_only``, ``unknown_channel``,
``category_redirected``, ``category_not_allowed``, ``severity_below_floor``,
``rate_limited``, or ``ok``.
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field

from rugmunchbot.channels.routing.table import (
    REDIRECTS,
    SEVERITY_RANK,
    ChannelRoute,
    policy_for,
)

#: Trailing window for the per-channel hourly cap.
WINDOW_SECONDS = 3600

#: channel -> send timestamps inside the current window.
_BUCKETS: dict[str, deque[float]] = {}


@dataclass(frozen=True, slots=True)
class Decision:
    """Outcome of one routing decision."""

    channel: str
    category: str
    severity: str
    allowed: bool
    live: bool
    reason: str
    redirect: str | None = None
    route: ChannelRoute | None = field(default=None)


def _dry_run(channel: str) -> bool:
    """True when *channel* is in dry-run mode (the default)."""
    per_channel = os.getenv(f"RMI_CHANNEL_{channel.upper()}_DRY_RUN")
    raw = per_channel if per_channel is not None else os.getenv("RMI_CHANNEL_DRY_RUN", "1")
    return raw.strip().lower() not in ("0", "false", "no", "off")


def is_live(channel: str) -> bool:
    """True when *channel* may send for real (dry-run OFF)."""
    return not _dry_run(channel)


def _rate_ok(channel: str, limit: int, now: float) -> bool:
    """Sliding-window check; records the send when it fits."""
    if limit <= 0:
        return False
    bucket = _BUCKETS.setdefault(channel, deque())
    while bucket and now - bucket[0] >= WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def decide(
    channel: str,
    category: str,
    severity: str = "high",
    *,
    now: float | None = None,
    count_rate: bool = True,
) -> Decision:
    """Decide whether one item may be posted to *channel*.

    ``count_rate=False`` evaluates policy only (used by the dry-run report so
    proving four channels does not consume their real hourly budget).
    """
    stamp = time.time() if now is None else now
    policy = policy_for(channel)
    live = not _dry_run(channel)
    category = (category or "").strip().lower()
    severity = (severity or "").strip().lower()

    def deny(reason: str, redirect: str | None = None) -> Decision:
        return Decision(channel, category, severity, False, live, reason, redirect, policy)

    if policy is None:
        return deny("unknown_channel")
    if policy.archived:
        return deny("archived")
    if policy.manual_only:
        return deny("manual_only")
    if policy.categories is not None and category not in policy.categories:
        redirect = REDIRECTS.get(category)
        return deny("category_redirected" if redirect else "category_not_allowed", redirect)
    if SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(policy.min_severity, 0):
        return deny("severity_below_floor")
    if count_rate and not _rate_ok(channel, policy.max_per_hour, stamp):
        return deny("rate_limited")
    return Decision(channel, category, severity, True, live, "ok", None, policy)


def reset_buckets() -> None:
    """Clear the sliding windows (tests / dry-run reports)."""
    _BUCKETS.clear()
