"""Tests: signal extraction + the two-independent-signal gate.

The refusal path is a first-class test: one heuristic never reaches main,
and two signals from the same family are still one observation.
"""

from __future__ import annotations

import pytest

from rugmunchbot.channels.shill_alerts.gate import DROP, MAIN, REVIEW, decide, describe
from rugmunchbot.channels.shill_alerts.signals import (
    DISCLOSURE,
    ONCHAIN,
    PROMOTION,
    MessageSignals,
    Signal,
    extract_signals,
    find_entity,
)

EVM = "0x7aE9b371f65B01277Ffaa2290a189a788A76170d"
SOL = "9xQeWvG816bUx9EPfEZgUZwQpC4d4NrN4qT5d1vTvGbZ"


def _msgs(entity: str = EVM, text: str = "") -> dict:
    return {
        "channel": "darkycalls",
        "message_id": "3307",
        "text": text,
        "link": "https://t.me/darkycalls/3307",
        "entity": entity,
    }


def test_find_entity_prefers_evm_and_ignores_link_fragments():
    assert find_entity(f"CA:{EVM}") == EVM
    assert find_entity("https://t.me/s/abc/123") == ""
    assert find_entity(f"mint {SOL}") == SOL


def test_address_alone_is_not_a_verdict():
    sig = extract_signals(_msgs(text=f"CA:{EVM}"))
    assert [s.name for s in sig.signals] == ["contract_address"]
    assert decide(sig).route == REVIEW


def test_two_signals_in_one_family_do_not_pass_the_gate():
    sig = extract_signals(_msgs(text=f"CA:{EVM} guaranteed 100x"))
    names = {s.name for s in sig.signals}
    assert {"contract_address", "hype_lexicon"} <= names
    verdict = decide(sig)
    assert verdict.route == REVIEW
    assert verdict.families == (PROMOTION,)
    assert "needs 2" in verdict.reasons[0]


def test_two_independent_families_reach_main():
    sig = extract_signals(
        _msgs(text=f"CA:{EVM} guaranteed 100x join my group"),
        evidence={"token_age_hours": 5},
    )
    verdict = decide(sig)
    assert ONCHAIN in verdict.families
    assert DISCLOSURE in verdict.families
    assert verdict.route == MAIN
    assert verdict.confidence >= 0.5
    assert describe(sig)


def test_disclosed_paid_promo_is_not_an_undisclosed_signal():
    disclosed = extract_signals(_msgs(text="paid promo #ad join my group"))
    undisclosed = extract_signals(_msgs(text="join my group"))
    assert "undisclosed_paid_promo" not in {s.name for s in disclosed.signals}
    assert "undisclosed_paid_promo" in {s.name for s in undisclosed.signals}


def test_onchain_and_identity_signals_fire_from_evidence():
    sig = extract_signals(
        _msgs(text=f"CA:{EVM}"),
        evidence={
            "onchain_risk": {"risk_level": "CRITICAL", "honeypot": True, "deployer_rugs": 3},
            "known_bad_actors": ["@darkycalls"],
        },
    )
    names = {s.name for s in sig.signals}
    assert {"onchain_risk", "known_bad_actor"} <= names
    assert decide(sig).route == MAIN


def test_cadence_spike_needs_a_ratio_not_a_raw_count():
    flat = extract_signals(
        _msgs(), evidence={"channel_rate_per_hour": 10, "channel_baseline_per_hour": 8}
    )
    spike = extract_signals(
        _msgs(), evidence={"channel_rate_per_hour": 30, "channel_baseline_per_hour": 8}
    )
    assert "cadence_spike" not in {s.name for s in flat.signals}
    assert "cadence_spike" in {s.name for s in spike.signals}


def test_no_signal_is_a_drop_never_a_main():
    sig = extract_signals({"channel": "x", "message_id": "1", "text": "gm"})
    verdict = decide(sig)
    assert verdict.route == DROP
    assert verdict.confidence == 0.0


@pytest.mark.parametrize(
    ("weight", "expected"),
    [(0.4, REVIEW), (0.6, MAIN)],
)
def test_confidence_floor_is_enforced_after_independence(weight, expected):
    signals = (
        Signal("a", PROMOTION, weight / 2, "x"),
        Signal("b", ONCHAIN, weight / 2, "y"),
    )
    sig = MessageSignals(channel="c", message_id="1", entity=EVM, signals=signals)
    assert decide(sig, min_confidence=0.5).route == expected
