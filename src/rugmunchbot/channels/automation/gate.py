"""Event dedupe (memory + durable) then per-channel hourly rate limit."""

from __future__ import annotations

from rugmunchbot.channels import scheduler_state
from rugmunchbot.smart_money.events import EventGate


class LaneGate(EventGate):
    """``EventGate`` that also refuses a key posted before the last restart.

    The durable check is additive: a Redis outage returns ``False`` from
    ``scheduler_state.has_posted``, so the in-memory layer keeps deduping
    within the process and never blocks a post on an outage.
    """

    def allow(self, key: str) -> str:
        """Return ``ok`` / ``dupe`` / ``rate`` (identical EventGate contract)."""
        if key and scheduler_state.has_posted(key):
            return "dupe"
        return super().allow(key)
