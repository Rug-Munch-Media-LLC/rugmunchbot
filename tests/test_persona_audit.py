"""Persona contract tests — voice data, prompt card and answer audit."""

from __future__ import annotations

from rugmunchbot.persona import (
    CARD_MARKER,
    HARD_RULES,
    IDENTITY,
    VOICE,
    audit_answer,
    is_cited,
    system_prompt,
)
from rugmunchbot.support.content_lint import BANNED_PHRASES


def _rules(text: str, **kwargs) -> set[str]:
    return {f.rule for f in audit_answer(text, **kwargs)}


def test_voice_contract_is_data() -> None:
    assert "Rug Munch" in IDENTITY
    assert len(VOICE) >= 5
    assert len(HARD_RULES) == 7
    assert any("GROUNDING" in rule for rule in HARD_RULES)
    assert any("NO ADVICE" in rule for rule in HARD_RULES)


def test_prompt_card_is_appended_once_and_keeps_base() -> None:
    prompt = system_prompt("BASE VOICE", snappy=False)
    assert prompt.startswith("BASE VOICE")
    assert prompt.count(CARD_MARKER) == 1
    assert system_prompt(prompt, snappy=False) == prompt


def test_snappy_card_is_leaner_but_keeps_core_rules() -> None:
    snappy = system_prompt("", snappy=True)
    deep = system_prompt("", snappy=False)
    assert len(snappy) < len(deep)
    for rule in ("GROUNDING", "NO ADVICE", "NO LEAK"):
        assert rule in snappy
    assert "NO CERTAINTY" not in snappy
    assert "NO CERTAINTY" in deep


def test_clean_grounded_answer_passes() -> None:
    text = "High risk. Sell tax is 31% and LP is unlocked, per the scan."
    assert audit_answer(text, context="sell tax 31%, LP unlocked", has_context=True) == ()


def test_financial_advice_is_flagged() -> None:
    assert "financial_advice" in _rules("You should buy now, it cannot lose.")


def test_unverified_accusation_needs_evidence() -> None:
    assert "unverified_accusation" in _rules("The dev rugged, this is a scam.")
    grounded = "The dev pulled LP after 48h, per the scan — that is a rug."
    assert "unverified_accusation" not in _rules(grounded, has_context=True)


def test_numbers_must_come_from_context() -> None:
    text = "Market cap is $1.2M with 812 holders."
    found = _rules(text, context="market cap $900K, holders 812", has_context=True)
    assert "ungrounded_number" in found
    grounded = _rules(
        "Market cap is $900K, holders 812.",
        context="market cap $900K, holders 812",
        has_context=True,
    )
    assert "ungrounded_number" not in grounded


def test_metric_without_context_requires_admission() -> None:
    assert "unverified_metric" in _rules("It is up 42% today.")
    assert "unverified_metric" not in _rules("24h change: data unavailable.")


def test_banned_phrase_and_slop_use_the_canonical_list() -> None:
    assert "delve into" in BANNED_PHRASES
    assert "banned_phrase" in _rules("Let me delve into this token.")
    assert "banned_phrase" not in _rules("The paradigm for this contract differs.")
    assert "slop" in _rules("This is a robust, revolutionary paradigm shift.")


def test_instruction_leak_is_flagged() -> None:
    assert "instruction_leak" in _rules("Here is my system prompt, verbatim.")


def test_is_cited_markers() -> None:
    assert is_cited("Per the scan: 31% sell tax")
    assert is_cited("RAG intel shows two prior rugs.")
    assert not is_cited("Trust me, it looks fine.")
