"""Contract tests for the single trending engine (bot side).

Locks the frozen engine payload shape, the paid-flag passthrough, the footer
count and the absence of any local scoring. No network: pure functions only.
"""

from __future__ import annotations

import pathlib

import pytest

from rugmunchbot.tools.trending_engine import paid_summary, ranked_tokens

PAYLOAD = {
    "chain": "solana",
    "engine": "rugbrain.trending_fusion/v1",
    "generated_at": "2026-10-25T10:00:00Z",
    "ranked": [
        {
            "rank": 1,
            "chain": "solana",
            "token_address": "So11111111111111111111111111111111111111112",
            "symbol": "PAID",
            "score": 0.73,
            "evidence": [{"source": "dexscreener", "signal": "volume_24h", "value": 6500000, "paid": True}],
            "paid": True,
            "paid_only": False,
            "disclosures": ["dexscreener_boost$500"],
        },
        {
            "rank": 2,
            "chain": "solana",
            "token_address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "symbol": "ORGANIC",
            "score": 0.51,
            "evidence": [{"source": "dexscreener", "signal": "volume_24h", "value": 1200000, "paid": False}],
            "paid": False,
            "paid_only": False,
            "disclosures": [],
        },
    ],
    "paid_only_excluded": [],
    "missing_sources": ["gmgn"],
    "stale": False,
}


def test_ranked_tokens_preserves_contract_fields():
    tokens = ranked_tokens(PAYLOAD)
    assert [t["symbol"] for t in tokens] == ["PAID", "ORGANIC"]
    assert tokens[0]["address"] == PAYLOAD["ranked"][0]["token_address"]
    assert tokens[0]["disclosures"] == ["dexscreener_boost$500"]
    assert tokens[0]["engine_meta"]["missing_sources"] == ["gmgn"]
    assert tokens[0]["engine_meta"]["stale"] is False


def test_paid_flag_passthrough_is_not_recomputed():
    tokens = ranked_tokens(PAYLOAD)
    assert [t["paid"] for t in tokens] == [True, False]
    assert tokens[0]["score"] == 0.73


def test_paid_footer_counts_engine_flags():
    assert paid_summary(PAYLOAD) == "1 of 2 are paid placements"
    clean = dict(PAYLOAD, ranked=[PAYLOAD["ranked"][1]])
    assert paid_summary(clean) is None


def test_no_local_score_computation_in_bot():
    root = pathlib.Path(__file__).resolve().parents[1] / "src/rugmunchbot"
    banned = ("S_vol", "_score_token", "trend_algo")
    offenders = [
        f"{p.relative_to(root)}:{needle}"
        for p in root.rglob("*.py")
        for needle in banned
        if needle in p.read_text(errors="ignore")
    ]
    assert offenders == [], f"local trending scoring survived: {offenders}"


@pytest.mark.parametrize("reason", ["transport:ConnectError", "http:503", "wrong_engine:None"])
def test_unavailable_reason_is_typed(reason):
    from rugmunchbot.tools.trending_engine import TrendingEngineUnavailable

    exc = TrendingEngineUnavailable(reason)
    assert exc.source == "rugbrain.trending_fusion"
    assert exc.reason == reason
