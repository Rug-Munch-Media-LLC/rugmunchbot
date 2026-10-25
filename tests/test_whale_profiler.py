"""Whale profiler — provider contract, degradation honesty, size boundaries.

No network: Ankr is mocked at the HTTP layer with respx (the repo's pattern),
so nothing is monkeypatched except the API-key env var.
"""

from __future__ import annotations

import pytest

from rugmunchbot.tools.whale_profiler import classify_whale_profile, normalize_wallet

WALLET = "0x" + "ab" * 20

# ── size classification boundaries ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0.0, "MINNOW"),
        (9_999.99, "MINNOW"),
        (10_000.0, "FISH"),
        (99_999.99, "FISH"),
        (100_000.0, "DOLPHIN"),
        (999_999.0, "DOLPHIN"),
        (1_000_000.0, "WHALE"),
        (9_999_999.0, "WHALE"),
        (10_000_000.0, "MEGA_WHALE"),
    ],
)
def test_size_class_boundaries(total, expected):
    behavior = classify_whale_profile({"total_usd": total, "token_count": 1}, [])
    assert behavior["size_class"] == expected
    assert behavior["degraded"] is False


def test_balance_unavailable_is_unknown_not_minnow():
    """A dead balance provider must NOT be reported as a MINNOW wallet."""
    behavior = classify_whale_profile(
        {"total_usd": 0, "token_count": 0}, [], balance_available=False
    )
    assert behavior["size_class"] == "UNKNOWN"
    assert behavior["degraded"] is True
    assert behavior["total_portfolio_usd"] is None


def test_malformed_balance_payload_does_not_raise():
    behavior = classify_whale_profile(
        {"total_usd": "not-a-number", "token_count": "x"}, ["garbage", None]
    )
    assert behavior["size_class"] == "MINNOW"
    assert behavior["total_portfolio_usd"] == 0.0
    assert behavior["recent_transfer_volume_usd"] == 0.0


# ── behavioral flags ────────────────────────────────────────────────────────


def test_flags_multi_chain_degen():
    behavior = classify_whale_profile({"total_usd": 5000, "token_count": 21}, [])
    assert "multi_chain_degen" in behavior["behavioral_flags"]


def test_flags_high_velocity_from_top_ten_volume():
    transfers = [{"value_usd": 20_000, "chain": "eth"}] * 6  # 120k in the top 10
    behavior = classify_whale_profile({"total_usd": 5000, "token_count": 2}, transfers)
    assert "high_velocity" in behavior["behavioral_flags"]


def test_flags_cross_chain_operator_needs_15_transfers_and_3_chains():
    fifteen = [{"value_usd": 1, "chain": c} for c in ("eth", "base", "sol")] * 5
    assert "cross_chain_operator" in classify_whale_profile({}, fifteen)["behavioral_flags"]
    fourteen = fifteen[:14]
    assert "cross_chain_operator" not in classify_whale_profile({}, fourteen)["behavioral_flags"]


# ── address normalization ───────────────────────────────────────────────────


def test_normalize_bare_evm_address_gets_0x():
    assert normalize_wallet("ab" * 20) == WALLET


def test_normalize_keeps_solana_address_intact():
    sol = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
    assert normalize_wallet(sol) == sol


def test_normalize_trims_whitespace():
    assert normalize_wallet(f"  {WALLET}  ") == WALLET


# ── function inventory ──────────────────────────────────────────────────────


def test_profiler_module_is_single_purpose():
    """The profiler owns classification only; the Ankr client is its own unit."""
    import rugmunchbot.tools._whale_ankr as ankr
    import rugmunchbot.tools.whale_profiler as profiler

    assert profiler.normalize_wallet.__module__ == "rugmunchbot.tools.whale_profiler"
    assert ankr.get_wallet_balance.__module__ == "rugmunchbot.tools._whale_ankr"
    assert not hasattr(profiler, "_ankr_call")
