"""Bot-facing banned phrases — the chrome list, built from what we shipped.

``support.content_lint.BANNED_PHRASES`` already governs *posts*. This module
extends it to *bot chrome* (replies, error text, onboarding, moderation) and
adds the offenders that actually shipped in handlers/: the eight copies of
"Please try again in a moment.", the rocket on a launch call, "Sequencing
token DNA", "No further action needed". One canonical list, imported not
copied, so weakening it here weakens it everywhere.

Single source: the post-level list is imported, never re-typed.
"""

from __future__ import annotations

import re
from typing import Final

from rugmunchbot.support.content_lint import BANNED_PHRASES

#: Chrome-specific offenders observed in the tree on 2026-10-25.
BOT_ONLY_PHRASES: Final[tuple[str, ...]] = (
    "as an ai",
    "as an ai language model",
    "i'm sorry",
    "i am sorry",
    "please try again in a moment",
    "please note",
    "great question",
    "let's dive in",
    "lfg",
    "to the moon",
    "no further action needed",
    "sequencing token dna",
    "i hope this helps",
    "feel free to",
)

#: Every banned phrase for bot-facing text: posts + chrome, de-duplicated.
BOT_BANNED_PHRASES: Final[tuple[str, ...]] = BANNED_PHRASES + tuple(
    p for p in BOT_ONLY_PHRASES if p not in BANNED_PHRASES
)

#: Emoji with no meaning on our surfaces — hype, not signal.
BANNED_EMOJI: Final[tuple[str, ...]] = ("\U0001f680", "\U0001f525", "\U0001f48e", "\U0001f319")

#: Non-phrase patterns. An exclamation mark is a shout, and we do not shout
#: (also covers "Free tier is already active!" and "!!!"). Matched against
#: string *values* only, never source lines, so `!=` is not a false positive.
BANNED_PATTERNS: Final[tuple[str, ...]] = (r"!", r"\b(?:lol|lmao)\b")


def _phrase_regex(phrase: str) -> re.Pattern[str]:
    """Word-boundary phrase regex so ``gm`` never matches ``gmail``."""
    return re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)


_PHRASE_RES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (p, _phrase_regex(p)) for p in BOT_BANNED_PHRASES
)
_PATTERN_RES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (p, re.compile(p, re.IGNORECASE)) for p in BANNED_PATTERNS
)


def banned_hits(text: str) -> tuple[str, ...]:
    """Return every banned phrase/pattern/emoji found in ``text``, sorted.

    Deterministic and side-effect free: same text, same tuple.
    """
    hits = [p for p, rx in _PHRASE_RES if rx.search(text)]
    hits.extend(p for p, rx in _PATTERN_RES if rx.search(text))
    hits.extend(e for e in BANNED_EMOJI if e in text)
    return tuple(sorted(set(hits)))
