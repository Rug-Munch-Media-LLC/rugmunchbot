"""Wave 37 — scan_token orchestrator contracts (was 9% covered).

The legacy multi-source scan is the /scan fallback + wallet/investigate
path. Offline: every provider boundary stubbed. Contracts:

* result skeleton: all keys present with honest defaults
* security_only=True skips market data (no DexScreener call) and runs
  ONLY the EVM security chain, clamping risk_score ≤ 100
* DexScreener happy path fills identity (name/symbol/chain) + market
  fields via the best-pair snapshot; $0-liquidity pairs[0] must NOT win
* volume authenticity: volume/mcap ratio > 5 raises a threat (+15)
* low liquidity vs mcap (<2%) raises a threat (+20)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.tools.scanner import _scans

ETH = "0x1234567890abcdef1234567890abcdef12345678"


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


class StubHTTP:
    """Scripted GET responses keyed by URL substring."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, timeout=None):
        self.calls.append(url)
        for frag, payload in self.routes.items():
            if frag in url:
                if isinstance(payload, Exception):
                    raise payload
                return _resp(payload)
        return _resp({}, status=404)


@pytest.fixture
def no_enrich(monkeypatch):
    """Silence GMGN/risk/solana/smart-money/RAG enrichment + sol price."""
    monkeypatch.setattr(
        _scans._scanner, "enrich_scan_with_gmgn", AsyncMock(side_effect=lambda r, a, c: r)
    )
    monkeypatch.setattr(_scans._scanner, "enrich_scan", None)
    monkeypatch.setattr(
        _scans._scanner, "enrich_scan_solana", AsyncMock(side_effect=lambda r, a, c: r)
    )
    monkeypatch.setattr(
        _scans._scanner, "enrich_scan_smart_money", AsyncMock(side_effect=lambda r, a, c: r)
    )
    monkeypatch.setattr(_scans, "get_sol_price", AsyncMock(return_value=100.0), raising=False)
    monkeypatch.setattr(_scans._scanner, "should_index_scan", lambda r, source="": False)


@pytest.mark.asyncio
async def test_security_only_skips_market_data(no_enrich):
    http = StubHTTP({})
    with patch.object(_scans._scanner, "get_shared_client", return_value=http):
        out = await _scans.scan_token(ETH, "ethereum", security_only=True)
    assert not any("dexscreener" in u.lower() for u in http.calls)
    assert out["symbol"] == "???"  # identity untouched
    assert out["risk_score"] <= 100


@pytest.mark.asyncio
async def test_dexscreener_happy_path_fills_identity_and_market(no_enrich):
    good_pair = {
        "chainId": "ethereum",
        "dexId": "uniswap",
        "pairAddress": "0xPAIR",
        "priceUsd": "2.5",
        "liquidity": {"usd": 400_000},
        "volume": {"h24": 90_000, "h1": 5_000},
        "marketCap": 8_000_000,
        "pairCreatedAt": 1700000000000,
        "baseToken": {"symbol": "WIN", "name": "Winner"},
        "priceChange": {"h1": 4.0, "h6": 6.0, "h24": 12.0},
    }
    zero_liq = dict(good_pair, liquidity={"usd": 0}, priceUsd="0.01")
    http = StubHTTP({DEX: {"pairs": [zero_liq, good_pair]}})
    with patch.object(_scans._scanner, "get_shared_client", return_value=http):
        out = await _scans.scan_token(ETH, "ethereum")
    assert out["symbol"] == "WIN"
    assert out["name"] == "Winner"
    assert out["price"] == 2.5  # best pair won, not pairs[0]
    assert out["liquidity"] == 400_000
    assert out["mcap"] == 8_000_000
    assert out["dex"] == "uniswap"
    assert out["price_change_24h"] == 12.0


DEX = "dexscreener"


@pytest.mark.asyncio
async def test_volume_authenticity_threat(no_enrich):
    """volume/mcap > 5 → wash-trading threat (+15)."""
    hot = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uni",
                "pairAddress": "P",
                "priceUsd": "1",
                "liquidity": {"usd": 50_000},
                "volume": {"h24": 3_000_000},  # ratio 60x vs 50k mcap? use mcap below
                "marketCap": 500_000,  # ratio = 6x > 5
                "baseToken": {"symbol": "HOT", "name": "Hot"},
            }
        ]
    }
    http = StubHTTP({DEX: hot})
    with patch.object(_scans._scanner, "get_shared_client", return_value=http):
        out = await _scans.scan_token(ETH, "ethereum")
    assert any("volume/mcap" in t.lower() for t in out["threats"])
    assert out["risk_score"] >= 15


@pytest.mark.asyncio
async def test_low_liquidity_threat(no_enrich):
    thin = {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uni",
                "pairAddress": "P",
                "priceUsd": "1",
                "liquidity": {"usd": 1_000},
                "volume": {"h24": 1_000},
                "marketCap": 1_000_000,  # liq/mcap = 0.1% < 2%
                "baseToken": {"symbol": "THIN", "name": "Thin"},
            }
        ]
    }
    http = StubHTTP({DEX: thin})
    with patch.object(_scans._scanner, "get_shared_client", return_value=http):
        out = await _scans.scan_token(ETH, "ethereum")
    assert any("low liquidity" in t.lower() for t in out["threats"])
    assert out["risk_score"] >= 20


@pytest.mark.asyncio
async def test_result_skeleton_always_complete(no_enrich):
    """Every provider failing → still a complete, well-formed result."""
    http = StubHTTP({DEX: ConnectionError("down")})
    with patch.object(_scans._scanner, "get_shared_client", return_value=http):
        out = await _scans.scan_token(ETH, "ethereum")
    for key in (
        "address",
        "chain",
        "name",
        "symbol",
        "price",
        "risk_score",
        "threats",
        "top_holders",
        "errors",
        "socials",
    ):
        assert key in out
    assert out["address"] == ETH
    assert any("DexScreener" in e for e in out["errors"])
