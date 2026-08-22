"""Test wave 6 — comparison detection + symbol-pair extraction.

Contracts from source docstrings + probed outputs (2026-08-22).
"""

from __future__ import annotations

from rugmunchbot.tools.intent import compare_pairs, detect_comparison


def test_detect_comparison_positive() -> None:
    assert detect_comparison("compare BONK vs WIF") is True


def test_detect_comparison_negative() -> None:
    assert detect_comparison("price of BONK") is False


def test_compare_pairs_dollar_symbols() -> None:
    # Only real $SYMBOL mentions are paired — never synthesized from prose.
    assert compare_pairs("compare $BONK vs $WIF") == [("BONK", "WIF")]


def test_compare_pairs_no_symbols_empty() -> None:
    assert compare_pairs("compare bonk vs wif") == []
