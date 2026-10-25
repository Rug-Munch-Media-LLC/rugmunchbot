"""Whale profiler — provider contract, degradation honesty, size boundaries.

No network: Ankr is mocked at the HTTP layer with respx (the repo's pattern),
so nothing is monkeypatched except the API-key env var.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from rugmunchbot.tools._whale_ankr import ANKR_MULTICHAIN
from rugmunchbot.tools.whale_profiler import (
    classify_whale_profile,
    normalize_wallet,
    profile_whale,
)

WALLET = "0x" + "ab" * 20


@pytest.fixture(autouse=True)
def _ankr_key(monkeypatch):
    monkeypatch.setenv("ANKR_API_KEY", "test-key")


def _ankr(handler):
    """Route the Ankr RPC to ``handler(request) -> dict`` (jsonrpc envelope)."""

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": handler(body)})

    return _side_effect


def _result_for(bal: dict, trx: dict):
    return lambda body: bal if body["method"] == "ankr_getAccountBalance" else trx


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


# ── provider paths ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_profile_success_sorts_and_caps_top_tokens():
    assets = [
        {"symbol": "BIG", "chainId": "eth", "balanceUsd": "50000000.5"},
        {"symbol": "SMALL", "chainId": "base", "balanceUsd": 1},
    ] + [{"symbol": f"T{i}", "chainId": "eth", "balanceUsd": 10} for i in range(12)]
    transfers = [
        {"blockchain": "eth", "from": "a", "to": "b", "usdValue": "250.5", "tokenSymbol": "ETH"}
    ]
    respx.post(ANKR_MULTICHAIN).mock(
        side_effect=_ankr(_result_for({"assets": assets}, {"transfers": transfers}))
    )

    profile = await profile_whale(WALLET)

    assert profile["wallet"] == WALLET
    assert profile["balance"]["token_count"] == 14
    assert len(profile["balance"]["top_tokens"]) == 10
    assert profile["balance"]["top_tokens"][0]["symbol"] == "BIG"
    assert profile["recent_transfers"][0]["value_usd"] == 250.5
    assert profile["behavior"]["size_class"] == "MEGA_WHALE"
    assert profile["degraded"] is False


@pytest.mark.asyncio
@respx.mock
async def test_empty_result_is_a_zero_wallet_not_an_error():
    respx.post(ANKR_MULTICHAIN).mock(side_effect=_ankr(_result_for({}, {})))
    profile = await profile_whale(WALLET)
    assert profile["balance"]["token_count"] == 0
    assert profile["recent_transfers"] == []
    assert profile["behavior"]["size_class"] == "MINNOW"
    assert profile["degraded"] is False


@pytest.mark.asyncio
@respx.mock
async def test_http_500_degrades_honestly():
    respx.post(ANKR_MULTICHAIN).mock(return_value=httpx.Response(500))
    profile = await profile_whale(WALLET)
    assert profile["behavior"]["size_class"] == "UNKNOWN"
    assert profile["degraded"] is True
    assert any("balance" in e for e in profile["errors"])


@pytest.mark.asyncio
@respx.mock
async def test_jsonrpc_error_degrades_honestly():
    respx.post(ANKR_MULTICHAIN).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "error": {"code": -1}})
    )
    profile = await profile_whale(WALLET)
    assert profile["degraded"] is True
    assert profile["behavior"]["size_class"] == "UNKNOWN"


@pytest.mark.asyncio
@respx.mock
async def test_malformed_upstream_fields_are_skipped():
    payload = {"assets": ["junk", {"symbol": "OK", "balanceUsd": "n/a"}], "transfers": ["x"]}
    respx.post(ANKR_MULTICHAIN).mock(side_effect=_ankr(_result_for(payload, payload)))
    profile = await profile_whale(WALLET)
    assert profile["balance"]["token_count"] == 1
    assert profile["balance"]["total_usd"] == 0.0
    assert profile["recent_transfers"] == []
    assert profile["degraded"] is False


@pytest.mark.asyncio
async def test_missing_api_key_is_degraded_not_a_crash(monkeypatch):
    monkeypatch.delenv("ANKR_API_KEY", raising=False)
    profile = await profile_whale(WALLET)
    assert profile["degraded"] is True
    assert profile["behavior"]["size_class"] == "UNKNOWN"
    assert profile["errors"]


@pytest.mark.asyncio
@respx.mock
async def test_transfer_page_size_is_capped_at_50():
    from rugmunchbot.tools._whale_ankr import get_wallet_transfers

    seen: dict = {}

    def _handler(body):
        seen[body["method"]] = body["params"]
        return {}

    respx.post(ANKR_MULTICHAIN).mock(side_effect=_ankr(_handler))
    await get_wallet_transfers(WALLET, limit=99)
    assert seen["ankr_getTransactionsByAddress"]["pageSize"] == 50


@pytest.mark.asyncio
@respx.mock
async def test_non_dict_result_is_tolerated():
    respx.post(ANKR_MULTICHAIN).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "result": ["odd"]})
    )
    profile = await profile_whale(WALLET)
    assert profile["balance"]["token_count"] == 0
    assert profile["degraded"] is False


# ── address normalization ───────────────────────────────────────────────────


def test_normalize_bare_evm_address_gets_0x():
    assert normalize_wallet("ab" * 20) == WALLET


def test_normalize_keeps_solana_address_intact():
    sol = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
    assert normalize_wallet(sol) == sol


def test_normalize_trims_whitespace():
    assert normalize_wallet(f"  {WALLET}  ") == WALLET
