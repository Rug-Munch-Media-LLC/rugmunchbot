"""Wave 36 — scanner _market.py coverage: DexScreener snapshot + Jupiter.

Pure-logic contracts for the market-data layer (previously 12% covered):

* _ds_market_snapshot picks the HIGHEST-LIQUIDITY pair, not pairs[0]
  (pump.fun bonding-curve pairs with $0 liquidity poison index 0)
* ties broken by 24h volume; non-dict pairs skipped; empty/non-list → {}
* honest None for missing fields; timeframe maps surface h1/h6/h24
* jupiter_price: happy path parse, fail-open on non-200/bad payload,
  empty-mint guard
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rugmunchbot.tools.scanner._market import (
    _ds_market_snapshot,
    jupiter_price,
)


def _pair(liq, vol24=1000.0, price="1.23", dex="raydium", sym="TEST"):
    return {
        "liquidity": {"usd": liq},
        "volume": {"h24": vol24, "h1": 10, "h6": 50},
        "priceUsd": price,
        "priceChange": {"h1": 1.0, "h6": 2.0, "h24": 3.0},
        "marketCap": 5_000_000,
        "pairAddress": "PAIRADDR",
        "dexId": dex,
        "pairCreatedAt": 1700000000000,
        "baseToken": {"symbol": sym, "name": "Test Token"},
    }


def test_best_pair_is_highest_liquidity_not_first():
    """pairs[0] is a $0-liquidity bonding curve; real pair has liquidity."""
    pairs = [_pair(0), _pair(250_000, price="9.99")]
    snap = _ds_market_snapshot(pairs)
    assert snap["liquidity"] == 250_000
    assert snap["price"] == 9.99


def test_tie_broken_by_volume():
    pairs = [_pair(100_000, vol24=500), _pair(100_000, vol24=900)]
    snap = _ds_market_snapshot(pairs)
    assert snap["volume_24h"] == 900


def test_non_dict_pairs_skipped_and_empty_cases():
    assert _ds_market_snapshot(None) == {}
    assert _ds_market_snapshot([]) == {}
    assert _ds_market_snapshot(["junk", 42]) == {}


def test_honest_none_for_missing_fields():
    snap = _ds_market_snapshot([{"liquidity": {"usd": 0}, "dexId": "x"}])
    assert snap["liquidity"] is None  # zero liquidity → None, not 0
    assert snap["price"] is None
    assert snap["dex"] == "x"


def test_timeframe_maps_surface():
    snap = _ds_market_snapshot([_pair(1000)])
    assert snap["volume_tf"]["h1"] == 10
    assert snap["price_change_tf"]["h24"] == 3.0


# ── Jupiter price fallback ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_jupiter_parses_v3_payload():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "So11111111111111111111111111111111111111112": {
            "usdPrice": 95.5,
            "liquidity": 761_881_179.11,
            "priceChange24h": 0.97,
        }
    }
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    out = await jupiter_price(client, "So11111111111111111111111111111111111111112")
    assert out == {
        "price_usd": 95.5,
        "liquidity": 761_881_179.11,
        "change_24h": 0.97,
    }


@pytest.mark.asyncio
async def test_jupiter_fail_open():
    bad = MagicMock()
    bad.status_code = 500
    client = MagicMock()
    client.get = AsyncMock(return_value=bad)
    assert await jupiter_price(client, "mint") is None

    junk = MagicMock()
    junk.status_code = 200
    junk.json.return_value = {"unexpected": "shape"}
    client.get = AsyncMock(return_value=junk)
    assert await jupiter_price(client, "mint") is None

    zero = MagicMock()
    zero.status_code = 200
    zero.json.return_value = {"mint": {"usdPrice": 0}}
    client.get = AsyncMock(return_value=zero)
    assert await jupiter_price(client, "mint") is None  # zero price rejected

    assert await jupiter_price(client, "") is None  # empty guard
