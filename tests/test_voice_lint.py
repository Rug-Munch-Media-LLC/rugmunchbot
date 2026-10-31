"""Banned-phrase gate for bot-facing strings.

Three assertions:

1. The oracle catches the phrases we actually shipped (proves it is not a
   tautology over an empty list).
2. No variant in ``voice._variants`` contains a banned phrase, and no SERIOUS
   variant contains emoji.
3. No *new* banned phrase appears in bot chrome. Legacy occurrences are pinned
   per (file, phrase) in ``tests/voice_legacy_offenders.txt``; a stale pin
   fails too, so the list can only shrink.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _voice_scan import scan_all

from rugmunchbot.voice import BANNED_EMOJI, VARIANTS, Register, banned_hits

BASELINE = Path(__file__).with_name("voice_legacy_offenders.txt")


@pytest.mark.parametrize(
    "shipped",
    [
        "Could not ban the user. Please try again in a moment.",
        "\U0001f680 <b>LAUNCH CALL \u00b7 {symbol}</b>",
        "\U0001f9ec Sequencing token DNA...",
        "Free tier is already active!",
        "No further action needed",
        "As an AI, I can only report the scan.",
        "I'm sorry, that token isn't indexed.",
    ],
)
def test_oracle_catches_shipped_offenders(shipped: str) -> None:
    assert banned_hits(shipped), f"oracle missed a real offender: {shipped!r}"


def test_voice_variants_are_clean() -> None:
    for key, (reg, variants) in VARIANTS.items():
        for text in variants:
            assert banned_hits(text) == (), f"{key} ({reg.value}) ships {banned_hits(text)}"
            if reg is Register.SERIOUS:
                assert not any(e in text for e in BANNED_EMOJI), f"{key} is SERIOUS with emoji"


def test_no_new_banned_phrase_in_bot_chrome() -> None:
    pinned = _baseline()
    observed = {
        f"{rel}::{phrase}"
        for rel, hits in scan_all().items()
        for h in hits
        for phrase in [h.split(":", 1)[1]]
    }
    new_hits = sorted(observed - pinned)
    assert not new_hits, "new banned phrase in bot-facing text:\n" + "\n".join(new_hits)
    stale = sorted(pinned - observed)
    assert not stale, "fixed strings still pinned — delete these lines:\n" + "\n".join(stale)


def _baseline() -> frozenset[str]:
    if not BASELINE.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )
