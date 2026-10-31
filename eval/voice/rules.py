"""Voice-eval policy: phrase lists, marker sets and the sub-score names.

This module is DATA, not logic - edit it to change what "good" measures.
Every entry is matched case-insensitively against the sample's current output.

The relative importance of the sub-scores is fleet calibration. It is resolved
at run time by ``eval/voice/policy.py`` (env ``RMB_VOICE_WEIGHTS_FILE``) and is
deliberately not published in this public repo.
"""

from __future__ import annotations

#: Slop / hype / false-promise phrases. A hit is a hard quality signal.
BANNED_PHRASES: tuple[str, ...] = (
    "to the moon",
    "guaranteed",
    "guaranteed 100x",
    "100x",
    "1000x",
    "ngmi",
    "wagmi",
    "we're so back",
    "we are so back",
    "buckle up",
    "diamond hands",
    "diamondhand",
    "down bad",
    "trust me bro",
    "this is financial advice",
    "can't lose",
    "can’t lose",  # noqa: RUF001 - the typographic apostrophe is the point: LLM output uses it
    "risk free",
    "risk-free",
    "free money",
    "easy money",
    "printing money",
)

#: Empty marketing filler. Density (hits per 100 words) drives the score.
CLICHES: tuple[str, ...] = (
    "in today's fast-paced",
    "fast-paced",
    "ever-evolving",
    "ever evolving",
    "landscape",
    "game-changer",
    "game changer",
    "let's dive in",
    "dive in",
    "unlock the power",
    "seamlessly",
    "revolutionize",
    "next level",
    "at the end of the day",
    "it's important to note",
    "important to note",
    "thrilled to announce",
    "excited to announce",
    "stay tuned",
    "in the world of",
    "navigate the",
    "skyrocket",
    "parabolic",
)

#: Word-boundary hedges. A verdict must choose; hedging is a scoring fault.
HEDGES: tuple[str, ...] = (
    "might",
    "may",
    "could be",
    "possibly",
    "perhaps",
    "arguably",
    "it seems",
    "seems to",
    "appears to",
    "likely",
    "maybe",
    "tends to",
    "we believe",
    "some say",
)

#: Joke markers. Fatal in a serious register (loss / security / moderation / error).
JOKE_MARKERS: tuple[str, ...] = (
    "lol",
    "lmao",
    "rofl",
    "😂",
    "🤣",
    "🙃",
    "😅",
    "rekt",
    "bruh",
    "oops",
    "whoops",
    "better luck next time",
    "skill issue",
    "have fun staying poor",
    "gg wp",
)

#: A call to action or a link - the third leg of headline + fact + CTA.
CTA_MARKERS: tuple[str, ...] = (
    "full story",
    "read more",
    "scan now",
    "tap ",
    "open app",
    "rugmunch.io",
    "→",
    "->",
    "view ",
    "try ",
)

#: Registers where a joke is a product defect, not a style choice.
SERIOUS_CATEGORIES: tuple[str, ...] = ("loss", "error", "moderation", "security")

#: The seven fixed sub-scores. Their relative importance is fleet calibration,
#: resolved by policy.load_weights() - never a literal in this public repo.
SUBSCORE_NAMES: tuple[str, ...] = (
    "banned",
    "cliche",
    "spread",
    "emoji",
    "hedge",
    "structure",
    "register",
)

#: CI gate threshold.
TARGET_MIN: float = 0.75
