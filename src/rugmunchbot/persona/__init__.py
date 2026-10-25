"""Canonical RugMunch persona — voice, guardrails, prompt card, answer audit.

Import from here; never re-declare the voice or the rules elsewhere.

* :func:`system_prompt` — append the guardrail card to a tier prompt (once).
* :func:`audit_answer` — deterministic guardrail violations in an answer.
* :func:`is_cited` — did the answer name a real source.
* :data:`IDENTITY`, :data:`VOICE`, :data:`HARD_RULES` — the contract itself.
"""

from __future__ import annotations

from rugmunchbot.persona._audit import (
    Finding as Finding,
    audit_answer as audit_answer,
    is_cited as is_cited,
)
from rugmunchbot.persona._prompt import CARD_MARKER as CARD_MARKER, system_prompt as system_prompt
from rugmunchbot.persona._rules import (
    ACCUSATION_CUES as ACCUSATION_CUES,
    ADVICE_CUES as ADVICE_CUES,
    CITATION_MARKERS as CITATION_MARKERS,
    HARD_RULES as HARD_RULES,
    IDENTITY as IDENTITY,
    UNKNOWN_PHRASES as UNKNOWN_PHRASES,
    VOICE as VOICE,
)

__all__ = [
    "ACCUSATION_CUES",
    "ADVICE_CUES",
    "CARD_MARKER",
    "CITATION_MARKERS",
    "HARD_RULES",
    "IDENTITY",
    "UNKNOWN_PHRASES",
    "VOICE",
    "Finding",
    "audit_answer",
    "is_cited",
    "system_prompt",
]
