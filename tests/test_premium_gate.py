"""Premium ($5/month) evidence gate — proof, source, time, why, materiality."""

from __future__ import annotations

import asyncio

import pytest

from rugmunchbot.smart_money._premium import premium_status
from rugmunchbot.smart_money.poster import publish
from rugmunchbot.support.premium_signal import (
    PREMIUM_MIN_SCORE,
    PREMIUM_MIN_USD,
    premium_verdict,
)

GOOD = {
    "wallet": "0xabc123abc123abc123abc123abc123abc123abcd",
    "token_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "tx_hash": "5" * 64,
    "source": "databus.smart_money",
    "updated_at": "2026-10-24T09:15:00Z",
    "why_it_matters": "first size entry since the unlock; invalidation below 0.42",
    "amount_usd": 240_000,
}


def test_full_evidence_item_is_premium() -> None:
    verdict = premium_verdict(GOOD)
    assert verdict.eligible
    assert verdict.score >= PREMIUM_MIN_SCORE
    assert verdict.missing == ()


@pytest.mark.parametrize(
    ("drop", "missing_fragment"),
    [
        ("tx_hash", "on-chain proof"),
        ("source", "named source"),
        ("updated_at", "timestamp"),
        ("why_it_matters", "why it matters"),
    ],
)
def test_any_missing_evidence_block_blocks_premium(drop: str, missing_fragment: str) -> None:
    item = {k: v for k, v in GOOD.items() if k != drop}
    if drop == "tx_hash":
        item.pop("wallet")
        item.pop("token_address")
    verdict = premium_verdict(item)
    assert not verdict.eligible
    assert any(missing_fragment in m for m in verdict.missing)


def test_dust_is_not_premium() -> None:
    item = {**GOOD, "amount_usd": PREMIUM_MIN_USD / 2}
    verdict = premium_verdict(item)
    assert not verdict.eligible
    assert any("size" in m for m in verdict.missing)


def test_why_it_matters_accepts_impact_aliases() -> None:
    item = {k: v for k, v in GOOD.items() if k != "why_it_matters"}
    item["impact"] = "adds to a cluster already long the sector"
    assert premium_verdict(item).eligible


def test_premium_status_is_json_safe() -> None:
    status = premium_status(GOOD)
    assert status["eligible"] is True
    assert status["missing"] == []
    assert isinstance(status["reasons"], list)


def test_publish_holds_low_signal_premium() -> None:
    result = asyncio.run(
        publish("post text", key="premium:low", premium=True, item={"amount_usd": 1})
    )
    assert result["status"] == "held_low_signal"
    assert result["premium"]["eligible"] is False


def test_publish_admits_high_signal_premium() -> None:
    result = asyncio.run(publish("post text", key="premium:high", premium=True, item=GOOD))
    assert result["status"] in {"dry_run", "sent"}
    assert result["premium"]["eligible"] is True
