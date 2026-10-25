"""Numeric grounding for the AI verdict paragraph.

The rule: the LLM may only cite numbers the scan returned. A sentence with an
unsupported number is dropped; a paragraph with nothing grounded collapses to
``""`` so the deterministic template verdict is used instead.
"""

from __future__ import annotations

from rugmunchbot.tools.scanner._ai_grounding import grounded_text

FACTS = {
    "symbol": "TEST",
    "risk_score": 72.0,
    "buy_tax": 0.05,
    "sell_tax": 0.05,
    "top_10_holder_pct": 61.2,
    "liquidity_usd": 1_200_000,
    "deployer_rugged_pct": 40.0,
}


def test_supported_numbers_survive() -> None:
    text = "Risk reads 72/100. Buy tax is 5% and top-10 hold 61%."
    assert grounded_text(text, FACTS) == text


def test_compact_usd_from_a_real_value_survives() -> None:
    assert grounded_text("Liquidity sits at $1.2M.", FACTS) == "Liquidity sits at $1.2M."


def test_invented_number_drops_only_its_sentence() -> None:
    text = "Risk reads 72/100. Holders are up 9420% this hour."
    assert grounded_text(text, FACTS) == "Risk reads 72/100."


def test_all_invented_collapses_to_empty() -> None:
    assert grounded_text("Sniper count is 31337 wallets.", FACTS) == ""


def test_empty_input_is_empty() -> None:
    assert grounded_text("", FACTS) == ""
    assert grounded_text("   ", FACTS) == ""


def test_24h_window_and_score_denominator_are_labels() -> None:
    text = "Volume faded over 24h. Risk reads 72/100."
    assert grounded_text(text, FACTS) == text


def test_fact_keys_permit_structural_numbers() -> None:
    """``top_10_holder_pct`` is a fact key, so "top 10" is not an invention."""
    assert grounded_text("Top 10 wallets hold 61%.", FACTS) == "Top 10 wallets hold 61%."
