"""Shared alert model (no I/O — importable from every lane module)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.channels.shill_alerts.gate import Verdict
    from rugmunchbot.channels.shill_alerts.signals import Signal


@dataclass(frozen=True, slots=True)
class Alert:
    """One entity/day alert, already gated. Rendering never re-scores."""

    entity: str
    chain: str
    day: str
    verdict: Verdict
    signals: tuple[Signal, ...]
    sources: tuple[str, ...]
    channels: tuple[str, ...]
    risk: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Canonical alert identity — the SAME key the ledger uses."""
        from rugmunchbot.channels.shill_alerts.dedupe import alert_key

        return alert_key(self.entity, self.day)
