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
                "published": "2026-08-30T14:32:00+00:00",
                "tx_hash": "0xabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabc1",
            },
        },
    )
    assert line is not None
    assert "Whale transfer" in line
    assert "500 ETH" in line
    # Wallet addresses (the actual signal) — full, copy-able.
    assert "0x1111111111111111111111111111111111111111" in line
    assert "0x2222222222222222222222222222222222222222" in line
    # Timestamp present.
    assert "🕐 Aug 30, 14:32 UTC" in line
    # Source link present (tx explorer).
    assert "etherscan.io/tx/" in line


def test_fmt_doc_wallet_less_transfer_dropped():
    """A whale transfer with NO wallet on either end is DROPPED (None) — a
    whale alert without the wallet is worthless noise, never posted."""
    line = _fmt_doc(
        "pid-x",
        {
            "content": "Whale transfer: 200 SOL on solana",
            "metadata": {"chain": "solana", "type": "large_transfer", "amount_sol": 200},
        },
    )
    assert line is None


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


# ── Alpha-quality contract (2026-08-31) ───────────────────────────────


def test_fmt_doc_carries_wallet_label():
    """A labeled wallet renders its label in the header line."""
    line = _fmt_doc(
        "pid-3",
        {
            "content": "Whale transfer",
            "metadata": {
                "chain": "ethereum",
                "type": "large_transfer",
                "amount_usd": 5_000_000,
                "token": "USDC",
                "from": "0x1111111111111111111111111111111111111111",
                "to": "0x2222222222222222222222222222222222222222",
                "label": "Binance Hot Wallet",
            },
        },
    )
    assert line is not None
    assert "Binance Hot Wallet" in line


def test_fmt_doc_carries_context():
    """Enrichment context (history / accumulating) renders as a 📊 line."""
    line = _fmt_doc(
        "pid-4",
        {
            "content": "Whale transfer",
            "metadata": {
                "chain": "base",
                "type": "large_transfer",
                "amount_usd": 1_200_000,
                "token": "USDC",
                "from": "0x1111111111111111111111111111111111111111",
                "to": "0x2222222222222222222222222222222222222222",
                "context": "smart-money wallet (5 trips · 80% win) · accumulating",
            },
        },
    )
    assert line is not None
    assert "📊" in line
    assert "accumulating" in line


def test_fmt_doc_carries_deep_dive_link():
    """Every whale alert links to the full analysis on rugmunch.io."""
    line = _fmt_doc(
        "pid-5",
        {
            "content": "Whale transfer",
            "metadata": {
                "chain": "ethereum",
                "type": "large_transfer",
                "amount_usd": 900_000,
                "token": "USDT",
                "from": "0x1111111111111111111111111111111111111111",
                "to": "0x2222222222222222222222222222222222222222",
            },
        },
    )
    assert line is not None
    assert "rugmunch.io/wallet/0x1111111111111111111111111111111111111111" in line
    assert "Full analysis" in line


def test_deep_dive_link_empty_without_wallet():
    from rugmunchbot.channels.scheduler._posts_whales import _deep_dive_link

    assert _deep_dive_link({"chain": "ethereum"}) == ""


def test_wallet_label_helpers_pure():
    from rugmunchbot.channels.scheduler._posts_whales import (
        _wallet_context,
        _wallet_label,
    )

    assert _wallet_label({}) == ""
    assert _wallet_label({"entity": "Coinbase"}) == "Coinbase"
    assert _wallet_context({}) == ""
    assert (
        _wallet_context({"history": "bought PEPE 3x before pumps"}) == "bought PEPE 3x before pumps"
    )
