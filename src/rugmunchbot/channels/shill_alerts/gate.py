"""The two-independent-signal gate: score, never verdict.

Rules (each one is a test):

* no signals                      -> ``drop``  (nothing to say)
* one signal                      -> ``review`` (review queue, never main)
* two signals, same family        -> ``review`` (one observation, not two)
* two+ families, weight < floor   -> ``review``
* two+ families, weight >= floor  -> ``main``

The gate never returns ``main`` for a single heuristic — that is the whole
contract, and the refusal path is exercised by ``test_shill_alerts_gate``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugmunchbot.channels.shill_alerts.signals import MessageSignals, Signal

MAIN = "main"
REVIEW = "review"
DROP = "drop"


@dataclass(frozen=True, slots=True)
class Verdict:
    """Routing decision for one entity, with the reasons that produced it."""

    route: str
    confidence: float
    families: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def is_main(self) -> bool:
        return self.route == MAIN


def _rows(signals: MessageSignals | tuple[Signal, ...]) -> tuple[Signal, ...]:
    """Accept either a scored message or a bare signal tuple (plan merges)."""
    return signals.signals if isinstance(signals, MessageSignals) else tuple(signals)


def _confidence(rows: tuple[Signal, ...]) -> float:
    """Additive weight, capped — corroboration raises it, never doubles it."""
    return round(min(0.98, sum(s.weight for s in rows)), 3)


def decide(
    signals: MessageSignals | tuple[Signal, ...],
    *,
    min_confidence: float = 0.5,
    min_families: int = 2,
) -> Verdict:
    """Route ``signals`` to main / review / drop.

    ``min_families`` is the independence requirement and is deliberately not
    configurable below 2 — one family is one observation.
    """
    required = max(2, min_families)
    rows = _rows(signals)
    families = tuple(sorted({s.family for s in rows}))
    confidence = _confidence(rows)
    names = tuple(s.name for s in rows)

    if not names:
        return Verdict(DROP, 0.0, (), ("no signal fired",))
    if len(families) < required:
        return Verdict(
            REVIEW,
            confidence,
            families,
            (f"{len(names)} signal(s) in {len(families)} family — needs {required}",),
        )
    if confidence < min_confidence:
        return Verdict(
            REVIEW,
            confidence,
            families,
            (f"confidence {confidence:.2f} below floor {min_confidence:.2f}",),
        )
    return Verdict(
        MAIN,
        confidence,
        families,
        (f"{len(names)} signals across {len(families)} independent families",),
    )


def describe(signals: MessageSignals) -> list[dict[str, Any]]:
    """JSON-safe signal rows for the alert payload / audit log."""
    return [
        {"name": s.name, "family": s.family, "weight": s.weight, "detail": s.detail}
        for s in signals.signals
    ]
