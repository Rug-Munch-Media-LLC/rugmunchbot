"""Compose the canonical guardrail card onto a tier system prompt.

The long tier keeps all seven hard rules; the snappy tier gets the five that
decide whether an answer is shippable (grounding, unknown, cite, no advice,
no leak) to protect prompt-eval latency. Both cards are derived from
:data:`._rules.HARD_RULES` — no second copy of the rules exists.
"""

from __future__ import annotations

from rugmunchbot.persona._rules import HARD_RULES, IDENTITY

#: Idempotency marker — a prompt that already carries the card is returned as-is.
CARD_MARKER = "RUGMUNCH GUARDRAILS"

#: HARD_RULES positions (0-based) the snappy tier keeps: grounding, unknown,
#: cite, no-advice, no-leak.
_SNAPPY_KEEP = (0, 1, 2, 3, 6)


def system_prompt(base: str, *, snappy: bool) -> str:
    """Return ``base`` with the guardrail card appended exactly once.

    ``base`` is the tier's own character prompt (``FALLBACK_SYSTEM_PROMPT`` or
    ``_SNAPPY_SYSTEM_PROMPT``). The card never replaces that voice — it pins
    the non-negotiables underneath it.
    """
    text = (base or "").rstrip()
    if CARD_MARKER in text:
        return text
    keep = _SNAPPY_KEEP if snappy else tuple(range(len(HARD_RULES)))
    rules = "\n".join(f"- {HARD_RULES[i]}" for i in keep)
    return f"{text}\n\n{IDENTITY}\n{CARD_MARKER} (non-negotiable):\n{rules}"


__all__ = ["CARD_MARKER", "system_prompt"]
