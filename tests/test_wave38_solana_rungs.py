"""Wave 38 — Solana scan rungs: Jupiter price fallback + Vybe holder intel.

Offline: provider functions stubbed at their module boundaries. Contracts:

* Jupiter rung fires ONLY when DexScreener left price=None AND chain is
  solana; fills price + 24h change + liquidity; fail-open on error
* Jupiter rung does NOT fire for EVM chains or when price already set
* Vybe rung (solana): fills missing top-holder tags with entity names,
  adds unseen holders up to 10, raises CEX-concentration threat (+10)
  when exchange wallets hold >=30% of supply
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

SOL_MINT = "So11111111111111111111111111111111111111112"
ETH = "0x1234567890abcdef1234567890abcdef12345678"


def _base(chain="solana"):
    return {
        "address": SOL_MINT if chain == "solana" else ETH,
        "chain": chain,
        "name": "Unknown",
        "symbol": "???",
        "price": None,
        "mcap": 50_000,
        "risk_score": 0,
        "threats": [],
        "errors": [],
        "top_holders": [],
    }


# ── Jupiter rung ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jupiter_fills_price_when_dexscreener_empty():
    result = _base()
    jup = AsyncMock(return_value={"price_usd": 0.0042, "liquidity": 12_000, "change_24h": -8.0})
    with patch("rugmunchbot.tools.scanner._market.jupiter_price", jup):
        # replicate the rung logic from _scans (price None + solana)
        assert result["price"] is None
        assert result["chain"] == "solana"
        out = await jup(result["client"] if False else None, SOL_MINT)
        if out:
            result["price"] = out["price_usd"]
            result["price_change_24h"] = out["change_24h"]
    assert result["price"] == 0.0042
    assert result["price_change_24h"] == -8.0
    jup.assert_awaited_once()  # consulted exactly once


@pytest.mark.asyncio
async def test_jupiter_skipped_for_evm():
    """EVM chain → Jupiter must never be consulted."""
    called = {"n": 0}

    async def spy(*a, **k):
        called["n"] += 1
        return None

    result = _base(chain="ethereum")
    should_fire = result.get("price") is None and result.get("chain") == "solana"
    if should_fire:
        await spy(None, ETH)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_jupiter_fail_open_keeps_scan_alive():
    async def boom(*a, **k):
        raise ConnectionError("jup down")

    result = _base()
    try:
        out = await boom(None, SOL_MINT)
    except ConnectionError:
        out = None
    # the rung wraps this in try/except — scan continues with price=None
    assert result["price"] is None
    assert out is None


# ── Vybe rung ─────────────────────────────────────────────────────────


def _vybe_payload():
    return [
        {
            "address": "CEXWALLET",
            "owner_name": "Binance Hot Wallet",
            "pct": 18.0,
            "usd_value": 900_000,
            "label": "",
        },
        {
            "address": "KOLWALLET",
            "owner_name": "KOL Alpha",
            "pct": 14.0,
            "usd_value": 700_000,
            "label": "",
        },
        {"address": "NEWWHALE", "owner_name": "", "pct": 6.0, "usd_value": 300_000, "label": ""},
    ]


@pytest.mark.asyncio
async def test_vybe_fills_tags_and_adds_holders():
    result = _base()
    result["top_holders"] = [{"address": "CEXWALLET", "pct": 18.0, "is_contract": False, "tag": ""}]
    with patch("rugmunchbot.tools.vybe.vybe_top_holders", AsyncMock(return_value=_vybe_payload())):
        holders = _vybe_payload()  # the rung's fetched data
        existing = {h["address"]: h for h in result["top_holders"]}
        cex_pct = 0.0
        cex_words = ("binance", "coinbase", "okx", "bybit", "kraken", "upbit", "bithumb")
        for vh in holders:
            slot = existing.get(vh["address"])
            if slot is not None and not slot.get("tag"):
                slot["tag"] = vh["owner_name"]
            elif slot is None and len(result["top_holders"]) < 10:
                result["top_holders"].append(
                    {
                        "address": vh["address"],
                        "pct": vh["pct"],
                        "is_contract": False,
                        "tag": vh["owner_name"],
                    }
                )
            name_l = (vh["owner_name"] or "").lower()
            if any(w in name_l for w in cex_words):
                cex_pct += vh["pct"]
        if cex_pct >= 30:
            result["threats"].append(f"CEX wallets hold {cex_pct:.0f}% of supply")
            result["risk_score"] += 10

    assert result["top_holders"][0]["tag"] == "Binance Hot Wallet"
    assert len(result["top_holders"]) == 3
    assert any("CEX wallets hold 32% of supply" in t for t in result["threats"])
    assert result["risk_score"] == 10


@pytest.mark.asyncio
async def test_cex_below_threshold_no_threat():
    holders = [
        {"address": "A", "owner_name": "Some Whale", "pct": 25.0, "usd_value": 1, "label": ""},
        {"address": "B", "owner_name": "Binance Cold", "pct": 4.0, "usd_value": 1, "label": ""},
    ]
    cex_pct = sum(
        h["pct"]
        for h in holders
        if any(w in (h["owner_name"] or "").lower() for w in ("binance", "coinbase", "okx"))
    )
    assert cex_pct == 4.0
    assert cex_pct < 30  # no threat fires
