"""Whale-alerts builder tests — formatting + dedup contracts."""

from __future__ import annotations

import pytest

from rugmunchbot.channels.scheduler._posts_whales import (
    _fmt_amount,
    _fmt_doc,
    build_whale_post,
)


def test_fmt_amount_stablecoin():
    assert _fmt_amount({"amount_usd": 12_500_000, "token": "USDT"}) == "$12.5M USDT"


def test_fmt_amount_btc():
    assert _fmt_amount({"amount_btc": 25.0}) == "25.0 BTC"


def test_fmt_amount_sol():
    assert _fmt_amount({"amount_sol": 4000}) == "4,000 SOL"


def test_fmt_doc_transfer_with_route():
    line = _fmt_doc(
        "pid-1",
        {
            "content": "Whale transfer: 500 ETH on ethereum",
            "metadata": {
                "chain": "ethereum",
                "type": "large_transfer",
                "amount_native": 500,
                "symbol": "ETH",
                "from": "0x1111111111111111111111111111111111111111",
                "to": "0x2222222222222222222222222222222222222222",
            },
        },
    )
    assert line is not None
    assert "Whale transfer" in line
    assert "500 ETH" in line
    assert "0x111111" in line
    assert "0x222222" in line


def test_fmt_doc_positioning():
    line = _fmt_doc(
        "pid-2",
        {
            "content": "Hyperliquid whale positioning — largest OI: BTC $2807M",
            "metadata": {"chain": "hyperliquid", "type": "positioning"},
        },
    )
    assert line is not None
    assert "Hyperliquid whales" in line


@pytest.mark.asyncio
async def test_build_post_fail_open(monkeypatch):
    """Dead qdrant → None, never raises."""
    import httpx

    class Dead:
        def __init__(self, *a, **kw): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, *a, **kw):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", Dead)
    assert await build_whale_post() is None
