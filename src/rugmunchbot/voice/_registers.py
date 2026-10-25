"""Register vocabulary — the three voices rugmunchbot is allowed to use.

Data only. A register is a *commitment*, not a mood: once a situation maps to
SERIOUS the writer loses the joke, the emoji and the exclamation mark. The
mapping lives here so a reviewer can read every surface's tone in one screen.
"""

from __future__ import annotations

from enum import Enum
from typing import Final


class Register(str, Enum):
    """The only three voices. ``str`` mixin keeps it JSON/log friendly."""

    WITTY = "witty"
    NEUTRAL = "neutral"
    SERIOUS = "serious"


#: Situation -> register. Situations are dotted and hierarchical so a new
#: leaf inherits its parent's register through the prefix walk in
#: :func:`rugmunchbot.voice.register_for`.
SITUATION_REGISTER: Final[dict[str, Register]] = {
    "command.ok": Register.WITTY,
    "command.progress": Register.NEUTRAL,
    "command.empty": Register.WITTY,
    "command.usage_error": Register.NEUTRAL,
    "plan.already_active": Register.NEUTRAL,
    "error.transient": Register.NEUTRAL,
    "error.user_input": Register.NEUTRAL,
    "scan.progress": Register.NEUTRAL,
    "scan.clean": Register.WITTY,
    "scan.caution": Register.NEUTRAL,
    "scan.danger": Register.SERIOUS,
    "alert.launch": Register.NEUTRAL,
    "alert.whale": Register.WITTY,
    "alert.rug": Register.SERIOUS,
    "alert.security": Register.SERIOUS,
    "onboarding.welcome": Register.WITTY,
    "onboarding.help_hint": Register.NEUTRAL,
    "moderation.notice": Register.SERIOUS,
    "moderation.appeal": Register.SERIOUS,
    "loss.disclosure": Register.SERIOUS,
    "market.recap": Register.WITTY,
    "market.noop": Register.NEUTRAL,
}

#: What each register must never do. Enforced by ``tests/test_voice_lint.py``
#: against the variant table, not just documented here.
NEVER_SAY: Final[dict[Register, tuple[str, ...]]] = {
    Register.WITTY: (
        "no exclamation marks",
        "no back-to-back jokes (one per message, max)",
        "no joke about a loss, a hack, or a ban",
        "no hype verbs (aping, sending it, mooning)",
    ),
    Register.NEUTRAL: (
        "no apology for doing the job",
        "no 'please try again in a moment'",
        "no emoji chains",
    ),
    Register.SERIOUS: (
        "no emoji at all",
        "no jokes, no asides, no winks",
        "no exclamation marks",
        "no euphemism for a loss (say the number, say what happened)",
    ),
}
