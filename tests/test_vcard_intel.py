"""Holder / bundle summary rows on the verdict card.

Covers the three behaviours the rows must keep:

  1. a reported holder count and fresh share render with provenance;
  2. a detected bundle renders its wallet count and confidence, a clean scan
     renders "none detected", and an absent payload renders the reason --
     never "none detected";
  3. the reasons row cites only returned facts and is suppressed for AVOID,
     whose why line already lists the hard fails.
"""

from __future__ import annotations

from rugmunchbot.formatters._vcard import format_verdict_card
from rugmunchbot.formatters._vcard_intel import intel_lines

TS = "12:00Z"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _base(**over: object) -> dict:
    payload = {
        "name": "Test Token",
        "symbol": "TEST",
        "address": SOL,
        "chain": "solana",
        "risk_score": 18,
        "honeypot": False,
        "mintable": False,
        "freezable": False,
        "holders": 3241,
        "liquidity_usd": 900_000,
    }
    payload.update(over)
    return payload


def test_holders_row_renders_count_and_fresh_share() -> None:
    lines = intel_lines(_base(wallet_metrics={"fresh_wallet_pct": 18.4}), TS)
    assert "<b>3,241</b>" in lines[0]
    assert "fresh 18%" in lines[0]
    assert "onchain" in lines[0]
    assert TS in lines[0]


def test_missing_holders_is_a_reason_not_a_zero() -> None:
    lines = intel_lines(_base(holders=None), TS)
    assert "0" not in lines[0]
    assert "\u2014" in lines[0]
    assert "no holder data" in lines[0]


def test_detected_bundle_renders_wallets_and_confidence() -> None:
    lines = intel_lines(
        _base(bundle_analysis={"is_bundled": True, "launch_buyers": 12, "confidence": 78}), TS
    )
    assert "<b>12</b> wallets" in lines[1]
    assert "78% conf" in lines[1]


def test_scanned_clean_bundle_says_none_detected() -> None:
    lines = intel_lines(_base(bundle_analysis={"is_bundled": False, "result": "scanned"}), TS)
    assert "none detected" in lines[1]


def test_absent_bundle_payload_is_never_read_as_clean() -> None:
    lines = intel_lines(_base(), TS)
    assert "none detected" not in lines[1]
    assert "bundle engine not run" in lines[1]


def test_bundle_engine_reason_is_surfaced() -> None:
    lines = intel_lines(_base(bundle_analysis={"reason": "no launch window data"}), TS)
    assert "no launch window data" in lines[1]


def test_reasons_cite_returned_facts_only() -> None:
    lines = intel_lines(
        _base(
            risk_score=64,
            bundle_analysis={"is_bundled": True, "launch_buyers": 9},
            holder_concentration={"top10_pct": 61.2},
            lp_locked=0.08,
        ),
        TS,
    )
    reasons = lines[-1]
    assert "Reasons" in reasons
    assert "top-10 hold 61%" in reasons


def test_avoid_suppresses_the_reasons_row() -> None:
    lines = intel_lines(_base(honeypot=True), TS, word="AVOID")
    assert all("Reasons" not in line for line in lines)


def test_card_carries_the_rows_above_the_clusters() -> None:
    card = format_verdict_card(
        _base(
            bundle_analysis={"is_bundled": True, "launch_buyers": 7},
            wallet_metrics={"fresh_wallet_pct": 22.0},
        )
    )
    assert "Holders" in card
    assert "7</b> wallets" in card
    assert card.index("Holders") < card.index("Market")
