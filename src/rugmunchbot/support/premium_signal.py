"""Premium eligibility gate — only evidence-backed alpha earns the $5 tier.

The paid tier is sold as *deep alpha with proof*: every item must carry an
on-chain handle, a named source, a timestamp and a reason it matters. A post
that cannot state those four things is a summary, not alpha, and stays in the
free lane. Nothing here guesses: a missing field LOWERS the score and is
listed in ``missing`` so the caller can log why an item was held.

Each evidence test is its own scorer in ``_EVIDENCE``; adding a signal is one
entry in that table, never another branch in the verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Final

#: Minimum evidence score for premium (0-100).
PREMIUM_MIN_SCORE: Final = 60

#: Material size threshold in USD — dust moves are not alpha.
PREMIUM_MIN_USD: Final = 50_000.0

_TIMESTAMP_KEYS: Final = ("ts", "block_time", "timestamp", "updated_at", "seen_at")
_WHY_KEYS: Final = ("why_it_matters", "why", "impact", "rationale", "note")
_USD_KEYS: Final = ("amount_usd", "usd_value", "size_usd", "value_usd")


@dataclass(frozen=True, slots=True)
class PremiumVerdict:
    """Why an item is (or is not) premium-grade — auditable, never a boolean."""

    score: int
    missing: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        """True only when the score clears the bar and nothing is missing."""
        return self.score >= PREMIUM_MIN_SCORE and not self.missing


@dataclass(frozen=True, slots=True)
class _Score:
    """One evidence test's outcome: points earned, or the gap that cost them."""

    points: int
    reason: str = ""
    missing: str = ""


def _has(item: dict[str, Any], key: str) -> bool:
    value = item.get(key)
    return value not in (None, "", 0, 0.0, [], {})


def _proof_score(item: dict[str, Any]) -> _Score:
    """On-chain proof (40) — a tx hash, or a wallet PLUS the token it moved."""
    if _has(item, "tx_hash") or (_has(item, "wallet") and _has(item, "token_address")):
        return _Score(40, reason="on-chain proof")
    return _Score(0, missing="on-chain proof (tx_hash or wallet+token_address)")


def _source_score(item: dict[str, Any]) -> _Score:
    """Named source (20) — a publication, URL or provider we can point at."""
    source = item.get("source") or item.get("source_url") or item.get("provider")
    if source:
        return _Score(20, reason=f"source {source}")
    return _Score(0, missing="named source")


def _timestamp_score(item: dict[str, Any]) -> _Score:
    """Timestamp (15) — without a moment, "alpha" cannot be checked."""
    if any(_has(item, key) for key in _TIMESTAMP_KEYS):
        return _Score(15, reason="timestamp")
    return _Score(0, missing="timestamp")


def _why_score(item: dict[str, Any]) -> _Score:
    """Why it matters (15) — the line that turns a movement into a thesis."""
    if any(_has(item, key) for key in _WHY_KEYS):
        return _Score(15, reason="why it matters")
    return _Score(0, missing="why it matters")


def _size_score(item: dict[str, Any]) -> _Score:
    """Material size (10) — dust moves are not alpha, and stay in free."""
    label = f"size >= ${PREMIUM_MIN_USD:,.0f}"
    usd = next((item.get(key) for key in _USD_KEYS if _has(item, key)), None)
    try:
        material = float(usd) >= PREMIUM_MIN_USD if usd is not None else False
    except (TypeError, ValueError):
        material = False
    return _Score(10, reason=label) if material else _Score(0, missing=label)


#: The evidence table, in score order. A new signal is one entry here.
_EVIDENCE: Final[tuple[Callable[[dict[str, Any]], _Score], ...]] = (
    _proof_score,
    _source_score,
    _timestamp_score,
    _why_score,
    _size_score,
)


def premium_verdict(item: dict[str, Any]) -> PremiumVerdict:
    """Score one feed item for the premium lane (deterministic, no LLM).

    Weights: on-chain proof 40, named source 20, timestamp 15, "why it
    matters" 15, material size 10. Missing any of the first four blocks
    eligibility outright — a premium post without proof or provenance is the
    one thing the $5 tier must never ship.
    """
    scores = tuple(score(item) for score in _EVIDENCE)
    return PremiumVerdict(
        score=sum(s.points for s in scores),
        missing=tuple(s.missing for s in scores if s.missing),
        reasons=tuple(s.reason for s in scores if s.reason),
    )


__all__ = ["PREMIUM_MIN_SCORE", "PREMIUM_MIN_USD", "PremiumVerdict", "premium_verdict"]
