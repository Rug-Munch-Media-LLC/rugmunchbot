"""Situation -> register resolution."""

from __future__ import annotations

from rugmunchbot.voice._registers import SITUATION_REGISTER, Register

_DEFAULT: Register = Register.NEUTRAL


def register_for(situation: str) -> Register:
    """Resolve a dotted situation to its register.

    Exact match first, then the longest dotted prefix (``alert.rug.solana``
    inherits ``alert.rug``). An unknown situation degrades to NEUTRAL rather
    than raising: a bot that crashes on prose is worse than a bot that is
    merely plain.
    """
    if situation in SITUATION_REGISTER:
        return SITUATION_REGISTER[situation]
    parts = situation.split(".")
    while parts:
        parts.pop()
        if not parts:
            break
        prefix = ".".join(parts)
        if prefix in SITUATION_REGISTER:
            return SITUATION_REGISTER[prefix]
    return _DEFAULT
