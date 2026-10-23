"""Unit tests for the price-alert rules (parse / quota / dedupe) — no DB, no network."""

from __future__ import annotations

from rugmunchbot.handlers.alerts._delete import _address_from
from rugmunchbot.handlers.alerts._manage import (
    ALERT_LIMITS,
    AlertSpec,
    active_alert_count,
    is_duplicate,
    parse_alert_args,
    quota_error,
)

EVM = "0x6982508145454ce325ddbe47a25d4ec3d2311933"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def test_parse_above_with_price() -> None:
    spec, err = parse_alert_args(["ABOVE", EVM, "0.005"])
    assert err is None
    assert spec == AlertSpec(direction="above", address=EVM, chain="ethereum", price=0.005)


def test_parse_change5_needs_no_price() -> None:
    spec, err = parse_alert_args(["change_5", SOL])
    assert err is None
    assert spec is not None
    assert spec.price is None
    assert spec.chain == "solana"


def test_parse_rejects_missing_price_for_above() -> None:
    spec, err = parse_alert_args(["BELOW", EVM])
    assert spec is None
    assert "target price" in (err or "")


def test_parse_rejects_bad_direction() -> None:
    spec, err = parse_alert_args(["SIDEWAYS", EVM, "1"])
    assert spec is None
    assert "Unknown direction" in (err or "")


def test_parse_rejects_non_numeric_and_non_positive_price() -> None:
    assert parse_alert_args(["ABOVE", EVM, "abc"])[0] is None
    assert parse_alert_args(["ABOVE", EVM, "0"])[0] is None
    assert parse_alert_args(["ABOVE", EVM, "-1"])[0] is None


def test_parse_rejects_unknown_chain_address() -> None:
    spec, err = parse_alert_args(["ANY", "not-an-address"])
    assert spec is None
    assert "Chain not detected" in (err or "")


def test_parse_rejects_too_few_args() -> None:
    spec, err = parse_alert_args(["ABOVE"])
    assert spec is None
    assert "Usage" in (err or "")


def test_free_tier_is_refused_by_quota() -> None:
    assert ALERT_LIMITS["free"] == 0
    assert "Scout tier" in (quota_error("free", 0) or "")


def test_quota_boundaries_per_tier() -> None:
    assert quota_error("scout", 9) is None
    assert "Alert limit reached (10 on your tier)" in (quota_error("scout", 10) or "")
    assert quota_error("hunter", 24) is None
    assert quota_error("pro", 99) is None
    assert quota_error("pro", 100) is not None


def test_active_alert_count_ignores_plain_watches_and_triggered() -> None:
    entries = [
        {"alert_price": 1.0, "is_active": 1},
        {"alert_price": None, "is_active": 1},  # plain /watch row
        {"alert_price": 2.0, "is_active": 0},  # already triggered
    ]
    assert active_alert_count(entries) == 1


def test_duplicate_detection_is_address_case_insensitive() -> None:
    entry = {
        "token_address": EVM.upper(),
        "alert_direction": "above",
        "alert_price": 0.005,
        "is_active": 1,
    }
    same = AlertSpec("above", EVM.lower(), "ethereum", 0.005)
    assert is_duplicate(entry, same) is True
    # different target or direction is a real change, not a duplicate
    assert is_duplicate(entry, AlertSpec("above", EVM, "ethereum", 0.006)) is False
    assert is_duplicate(entry, AlertSpec("below", EVM, "ethereum", 0.005)) is False
    # different token is never a duplicate
    assert is_duplicate(entry, AlertSpec("above", SOL, "solana", 0.005)) is False


def test_duplicate_detection_without_price() -> None:
    entry = {"token_address": EVM, "alert_direction": "any", "alert_price": None, "is_active": 1}
    assert is_duplicate(entry, AlertSpec("any", EVM, "ethereum", None)) is True


def test_address_picker_accepts_delete_prefix() -> None:
    assert _address_from(["delete", EVM]) == EVM
    assert _address_from(["delete"]) == ""
    assert _address_from([SOL]) == SOL
