"""Premium eligibility gate — only evidence-backed alpha earns the $5 tier.

The paid tier is sold as *deep alpha with proof*: every item must carry an
on-chain handle, a named source, a timestamp and a reason it matters. A post
that cannot state those four things is a summary, not alpha, and stays in the
free lane. Nothing here guesses: a missing field LOWERS the score and is
listed in ``missing`` so the caller can log why an item was held.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

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


def _has(item: dict[str, Any], key: str) -> bool:
    value = item.get(key)
    return value not in (None, "", 0, 0.0, [], {})


def premium_verdict(item: dict[str, Any]) -> PremiumVerdict:
    """Score one feed item for the premium lane (deterministic, no LLM).

    Weights: on-chain proof 40, named source 20, timestamp 15, "why it
    matters" 15, material size 10. Missing any of the first four blocks
    eligibility outright — a premium post without proof or provenance is the
    one thing the $5 tier must never ship.
    """
    score = 0
    reasons: list[str] = []
    missing: list[str] = []

    proof = _has(item, "tx_hash") or (_has(item, "wallet") and _has(item, "token_address"))
    if proof:
        score += 40
        reasons.append("on-chain proof")
    else:
        missing.append("on-chain proof (tx_hash or wallet+token_address)")

    source = item.get("source") or item.get("source_url") or item.get("provider")
    if source:
        score += 20
        reasons.append(f"source {source}")
    else:
        missing.append("named source")

    if any(_has(item, key) for key in _TIMESTAMP_KEYS):
        score += 15
        reasons.append("timestamp")
    else:
        missing.append("timestamp")

    if any(_has(item, key) for key in _WHY_KEYS):
        score += 15
        reasons.append("why it matters")
    else:
        missing.append("why it matters")

    usd = next((item.get(key) for key in _USD_KEYS if _has(item, key)), None)
    try:
        material = float(usd) >= PREMIUM_MIN_USD if usd is not None else False
    except (TypeError, ValueError):
        material = False
    if material:
        score += 10
        reasons.append(f"size >= ${PREMIUM_MIN_USD:,.0f}")
    else:
        missing.append(f"size >= ${PREMIUM_MIN_USD:,.0f}")

    return PremiumVerdict(score=score, missing=tuple(missing), reasons=tuple(reasons))


__all__ = ["PREMIUM_MIN_SCORE", "PREMIUM_MIN_USD", "PremiumVerdict", "premium_verdict"]
