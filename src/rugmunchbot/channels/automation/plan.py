"""One candidate channel post plus the provenance telemetry must carry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PostPlan:
    """A rendered post awaiting the gate.

    ``event_key`` is the dedupe identity and never contains a timestamp.
    ``source`` / ``observed_at`` / ``risk_note`` mirror the provenance block
    the renderer already prints, so a tick summary never re-parses HTML.
    """

    lane: str
    event_key: str
    post: Any
    channel_role: str = "alpha"
    source: str = ""
    observed_at: str = ""
    risk_note: str = ""

    def as_dict(self) -> dict[str, Any]:
        """JSON-able summary for ``channels:tick`` telemetry (no post body)."""
        return {
            "lane": self.lane,
            "event_key": self.event_key,
            "channel_role": self.channel_role,
            "source": self.source,
            "observed_at": self.observed_at,
            "risk_note": self.risk_note,
        }
