"""Whale profiler — Ankr provider contract (success, outage, malformed).

HTTP-level mocks only (respx); the API key is the sole environment seam.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from rugmunchbot.tools._whale_ankr import ANKR_MULTICHAIN, get_wallet_transfers
from rugmunchbot.tools.whale_profiler import profile_whale

WALLET = "0x" + "ab" * 20


@pytest.fixture(autouse=True)
def _ankr_key(monkeypatch):
    monkeypatch.setenv("ANKR_API_KEY", "test-key")


def _ankr(handler):
    """Route the Ankr RPC to ``handler(body) -> dict`` (jsonrpc envelope)."""

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": handler(body)})

    return _side_effect


def _result_for(bal: dict, trx: dict):
    return lambda body: bal if body["method"] == "ankr_getAccountBalance" else trx


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
