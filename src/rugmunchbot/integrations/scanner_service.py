"""Scanner service adapter — OSS fail-open.

The live bot re-scans flagged addresses via the private scanner API
(``app.domains.scanner.service.scan_token``). OSS returns a neutral result
with no risk flags and no safety score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScanOutcome:
    risk_flags: list[str] = field(default_factory=list)
    safety_score: float | None = None
    verdict: str = "unscanned"


async def scan_token(token_address: str, chain: str, **kwargs: Any) -> ScanOutcome:
    """No-op: return a neutral outcome (never asserts risk in OSS)."""
    return ScanOutcome()


__all__ = ["ScanOutcome", "scan_token"]
