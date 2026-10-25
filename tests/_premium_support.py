"""Shared premium-test helpers (extracted when the monolith was split, 2026-10-25).

Both `_Clock` and `_open_gate` lived in the pre-split module; part3 kept using
them after the split moved the definitions into part2, which made two tests
error at collection (NameError/ImportError). One home, imported by both parts.
"""

from __future__ import annotations

from rugmunchbot.premium import watch_alerts


class _Clock:
    def __init__(self, at: float = 0.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def _open_gate(tier: str = "pro", clock=None) -> watch_alerts.AlertGate:
    return watch_alerts.AlertGate(
        quiet=watch_alerts.QuietHours(start=0, end=0),  # window disabled
        tier=tier,
        clock=clock or _Clock(),
    )
