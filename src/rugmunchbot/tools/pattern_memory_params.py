from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PatternRecallQuery:
    address: str = ""
    symbol: str = ""
    chain: str = ""
    signals: dict[str, float] | None = None
    top_k: int = 5
    min_similarity: float = 0.5
