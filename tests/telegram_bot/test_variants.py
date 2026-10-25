"""Determinism, coverage, voice and numeric-stability tests for reply variants."""
from __future__ import annotations

import re

import pytest

from rugmunchbot.bot.variants import POOLS, render_variant

BANNED = ("as an ai", "i think", "probably", "maybe", "let's dive",
          "in today's", "game-changer", "unleash", "!!")


def _situations():
    return sorted(POOLS)


@pytest.mark.parametrize("situation", _situations())
def test_same_identifiers_same_output(situation):
    a = render_variant(situation, chat_id=1, message_id=2, subject="X", addr="X",
                       verdict="HIGH", score=87, retry=30, symbol="X",
                       target="1.5", detail="X", feature="X", tier="PRO")
    b = render_variant(situation, chat_id=1, message_id=2, subject="X", addr="X",
                       verdict="HIGH", score=87, retry=30, symbol="X",
                       target="1.5", detail="X", feature="X", tier="PRO")
    assert a == b


@pytest.mark.parametrize("situation", _situations())
def test_every_variant_reachable(situation):
    seen = {render_variant(situation, chat_id=c, message_id=c, subject="X", addr="X",
                           verdict="HIGH", score=87, retry=30, symbol="X",
                           target="1.5", detail="X", feature="X", tier="PRO")
            for c in range(500)}
    assert len(seen) == len(POOLS[situation]), (situation, len(seen))


@pytest.mark.parametrize("situation", _situations())
def test_different_users_see_different_phrasing(situation):
    outs = {render_variant(situation, chat_id=c, message_id=1, subject="X", addr="X",
                           verdict="HIGH", score=87, retry=30, symbol="X",
                           target="1.5", detail="X", feature="X", tier="PRO")
            for c in range(80)}
    assert len(outs) > 1


@pytest.mark.parametrize("situation", _situations())
def test_no_banned_phrase(situation):
    for template in POOLS[situation]:
        low = template.lower()
        for phrase in BANNED:
            assert phrase not in low, (situation, phrase, template)


@pytest.mark.parametrize("chart_id,score,verdict", [(1, 87, "HIGH"), (2, 0, "LOW"), (3, 100, "CRITICAL")])
def test_numeric_stability_across_variants(chart_id, score, verdict):
    for chat in range(12):
        out = render_variant("risk.verdict", chat_id=chat, message_id=9,
                             verdict=verdict, score=score)
        assert str(score) in out
        assert verdict in out
        assert re.search(r"\b\d+\b", out)
