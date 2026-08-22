"""Test wave 26 — pricing + pattern-memory pure helpers.

Contracts read from source (2026-08-22). Pure functions only.
"""

from __future__ import annotations

import math

import pytest

from rugmunchbot.services.pricing import (
    _looks_like_address,
    _raw_pairs,
    _raw_pairs_from_backend,
    _to_float as _price_to_float,
)
from rugmunchbot.tools.pattern_memory import (
    _clean_signals,
    _similarity,
    _to_float as _pm_to_float,
)

# ── pricing coercion / pair extraction ───────────────────────────────────────


def test_price_to_float_zero_on_junk() -> None:
    assert _price_to_float(None) == 0.0
    assert _price_to_float("12.5") == 12.5
    assert _price_to_float("junk") == 0.0


def test_raw_pairs_keeps_dicts_only() -> None:
    pair = {"pairAddress": "0xp"}
    assert _raw_pairs({"pairs": [pair, "junk", 42]}) == [pair]
    assert _raw_pairs(None) == []
    assert _raw_pairs({}) == []
    assert _raw_pairs({"pairs": "junk"}) == []


def test_raw_pairs_from_backend_unwraps_data_nesting() -> None:
    direct = {"pairs": [{"a": 1}]}
    assert _raw_pairs_from_backend(direct) == [{"a": 1}]
    nested = {"data": {"pairs": [{"b": 2}]}}
    assert _raw_pairs_from_backend(nested) == [{"b": 2}]
    # Direct pairs win over the legacy nesting when both exist.
    both = {"pairs": [{"a": 1}], "data": {"pairs": [{"b": 2}]}}
    assert _raw_pairs_from_backend(both) == [{"a": 1}]
    assert _raw_pairs_from_backend(None) == []
    assert _raw_pairs_from_backend({"data": "junk"}) == []


def test_looks_like_address_detection_rules() -> None:
    evm = "0x" + "ab" * 20
    assert _looks_like_address(evm) is True
    assert _looks_like_address("0xshort") is False  # too short for EVM
    sol = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    assert _looks_like_address(sol) is True  # 44-char base58
    assert _looks_like_address("1" * 40) is False  # all digits -> not an address
    assert _looks_like_address("PEPE") is False
    assert _looks_like_address("  " + sol + "  ") is True  # stripped first


# ── pattern memory signal hygiene ────────────────────────────────────────────


def test_pm_to_float_none_semantics() -> None:
    # NOTE: pattern_memory's _to_float returns None on junk — the OPPOSITE
    # of services.pricing's 0.0 convention.
    assert _pm_to_float(None) is None
    assert _pm_to_float("") is None
    assert _pm_to_float("2.5") == 2.5
    assert _pm_to_float("junk") is None


def test_clean_signals_keeps_finite_numerics_only() -> None:
    out = _clean_signals(
        {
            "change_24h": 12.5,
            "bad_str": "junk",
            "nan": math.nan,
            "inf": math.inf,
            "none": None,
            7: 3.0,  # non-str key coerced to str
        }
    )
    assert out == {"change_24h": 12.5, "7": 3.0}
    assert _clean_signals("not a dict") == {}
    assert _clean_signals(None) == {}


def test_similarity_no_overlap_is_zero() -> None:
    assert _similarity({"a": 1.0}, {"b": 1.0}) == 0.0
    assert _similarity({}, {}) == 0.0


def test_similarity_relative_error_scoring() -> None:
    assert _similarity({"a": 200.0}, {"a": 210.0}) == pytest.approx(1 - 10 / 210)
    # Opposite directions clamp at zero (+200% vs -85%).
    assert _similarity({"a": 200.0}, {"a": -85.0}) == 0.0
    # Only overlapping keys count.
    out = _similarity({"a": 200.0, "b": 100.0}, {"a": 210.0, "c": 5.0})
    assert out == pytest.approx(1 - 10 / 210)


def test_similarity_near_zero_denominators() -> None:
    assert _similarity({"x": 0.0}, {"x": 0.0}) == 1.0  # both zero = perfect match
    assert _similarity({"x": 0.0}, {"x": 5.0}) == 0.0
