"""Flow-evidence contracts — real Data API trade shape, evidence wording only."""

from __future__ import annotations

import json
from pathlib import Path

from rugmunchbot.polymarket.flow import build_flow_evidence

_FIX = Path(__file__).parent / "fixtures" / "polymarket"


def _trades() -> list[dict]:
    return json.loads((_FIX / "trades.json").read_text())


def _counts() -> dict[str, int]:
    return json.loads((_FIX / "traded_counts.json").read_text())


def test_notional_is_shares_times_price_and_small_trades_drop() -> None:
    evidence = build_flow_evidence("0xaaa111", _trades(), _counts(), min_notional_usd=10_000)
    notionals = [t.notional_usd for t in evidence.trades]
    assert 15_500.0 in notionals
    assert 11_400.0 in notionals
    assert 240.0 not in notionals  # 400 sh x 0.60 below threshold
    assert evidence.total_notional_usd == 51_100.0


def test_junk_rows_are_ignored_not_fatal() -> None:
    evidence = build_flow_evidence("0xaaa111", _trades(), _counts(), min_notional_usd=10_000)
    assert all(t.wallet for t in evidence.trades)
    assert len(evidence.trades) == 4


def test_low_history_uses_market_count_and_skips_unknown() -> None:
    evidence = build_flow_evidence(
        "0xaaa111", _trades(), _counts(), min_notional_usd=10_000, low_history_max_markets=5
    )
    assert evidence.low_history_wallets == ("0x6100acd1f9ebe426750cb5cf7bc89e262beaed95",)
    # wallet absent from traded_counts is unknown, never assumed low-history
    assert all(w in _counts() for w in evidence.low_history_wallets)


def test_same_outcome_concentration_needs_three_wallets() -> None:
    evidence = build_flow_evidence("0xaaa111", _trades(), _counts(), min_notional_usd=10_000)
    assert len(evidence.same_outcome_wallets) == 3
    three_only = [
        t for t in _trades() if t["proxyWallet"] != "0x7a11c0de55aa44bb33cc22dd11ee00ff99887766"
    ]
    narrow = build_flow_evidence("0xaaa111", three_only, _counts(), min_notional_usd=10_000)
    # two wallets on one outcome is ordinary flow, not concentration evidence
    assert narrow.same_outcome_wallets == ()


def test_evidence_note_never_accuses() -> None:
    evidence = build_flow_evidence("0xaaa111", _trades(), _counts())
    text = evidence.limits_note.lower()
    assert "public on-chain evidence only" in text
    for banned in ("insider", "manipulat", "scam", "guilty"):
        assert banned not in text
    assert "not exhaustive" in evidence.window_note
