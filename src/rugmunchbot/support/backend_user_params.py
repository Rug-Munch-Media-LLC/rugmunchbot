from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WatchlistAlertSpec:
    entry_id: str
    direction: str
    price: float
