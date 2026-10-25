"""Numeric grounding for the AI verdict paragraph.

The snappy LLM is instructed to cite only returned facts; this module checks
that instruction instead of trusting it. Every number in the generated text
must exist in the fact projection -- either literally, or as a derived form of
a real value (percent, compact thousands/millions, rounded).

A sentence carrying any unsupported number is dropped. If no sentence
survives, the caller renders nothing for the AI block and the deterministic
template verdict stands -- an invented metric never reaches a reader.

One public entry point: :func:`grounded_text`.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["grounded_text"]

#: Numbers that are labels, not claims: the 0-100 score scale and the 24h window.
SCALE_TOKENS = frozenset({"100", "24"})
NUMBER = re.compile(r"\d+(?:\.\d+)?")
SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _numbers(text: str) -> set[str]:
    """Every numeric token in ``text`` (thousands separators removed)."""
    return set(NUMBER.findall(text.replace(",", "")))


def _values(value: Any) -> Iterator[float]:
    """Every numeric leaf in a nested fact projection."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _values(item)


def _forms(value: float) -> Iterator[str]:
    """Rendered forms a writer may legitimately produce for ``value``."""
    for scaled in (value, value * 100, value * 1000):
        yield f"{scaled:.0f}"
        yield f"{scaled:.1f}"
        yield f"{scaled:.2f}"
    for divisor in (1e3, 1e6, 1e9):
        yield f"{value / divisor:.1f}"
        yield f"{value / divisor:.2f}"


def _allowed(facts: dict[str, Any]) -> set[str]:
    """Numbers the writer may cite: fact keys, fact values and derived forms."""
    allowed = _numbers(json.dumps(facts, default=str)) | set(SCALE_TOKENS)
    for value in _values(facts):
        allowed.update(_forms(value))
    return allowed


def grounded_text(text: str, facts: dict[str, Any]) -> str:
    """Keep only the sentences whose numbers every fact supports.

    Args:
        text: LLM output (plain text, no markup).
        facts: The same projection handed to the model.

    Returns:
        The surviving sentences joined by a space; ``""`` when nothing is
        grounded.
    """
    body = (text or "").strip()
    if not body:
        return ""
    allowed = _allowed(facts)
    kept = [s for s in SENTENCE.split(body) if _numbers(s) <= allowed]
    return " ".join(kept).strip()
