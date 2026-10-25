"""Shared premium-test helpers (moved out of the split modules, 2026-10-25)."""

from __future__ import annotations


class _Clock:
    def __init__(self, at: float = 0.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at
