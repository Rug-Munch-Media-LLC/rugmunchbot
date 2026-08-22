"""Wave 10 — handlers._trending: multi-source trending fetchers.

All expectations probed against the implementation before asserting (oracle
policy). HTTP seams monkeypatched at the module namespace (http_get_json /
http_get_any / get_shared_client).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import rugmunchbot.handlers._trending as tr

# ── CoinGecko trending-news fallback ────────────────────────────────────


async def test_cg_trending_maps_coins_to_pseudo_news(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cg(url: str, timeout: int = 8) -> dict[str, Any]:
        return {
            "coins": [{"item": {"name": "WIF", "symbol": "WIF", "id": "dogwifcoin", "score": 3}}]
        }

    monkeypatch.setattr(tr, "http_get_json", fake_cg)
    items = await tr._fetch_cg_trending_news()
    assert items == [
        {
            "title": "WIF (WIF) trending now",
            "source": "Trending",
            "published_at": None,
            "url": "https://www.coingecko.com/en/coins/dogwifcoin",
            "score": 3,
        }
    ]


@pytest.mark.parametrize("body", [None, {}, {"coins": []}])
async def test_cg_trending_fails_open(body: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cg(url: str, timeout: int = 8) -> Any:
        return body

    monkeypatch.setattr(tr, "http_get_json", fake_cg)
    assert await tr._fetch_cg_trending_news() is None


# ── DexScreener boosted-tokens pipeline ─────────────────────────────────


class _FakeClient:
    """Canned pair details keyed by token address suffix."""

    def __init__(self) -> None:
        self.pairs: dict[str, dict[str, Any]] = {}

    def add(
        self,
        addr: str,
        name: str = "Good Token",
        symbol: str = "GOOD",
        liq: float = 50_000.0,
        vol: float = 90_000.0,
    ) -> None:
        self.pairs[addr] = {
            "chainId": "solana",
            "dexId": "raydium",
            "baseToken": {"name": name, "symbol": symbol},
            "priceUsd": "0.01",
            "liquidity": {"usd": liq},
            "volume": {"h24": vol},
            "priceChange": {"h24": 5.0, "h1": 1.0, "d7": -2.0},
        }

    async def get(self, url: str, timeout: int = 8) -> httpx.Response:
        addr = url.rsplit("/", 1)[1]
        return httpx.Response(
            200, json={"pairs": [self.pairs[addr]]}, request=httpx.Request("GET", url)
        )


def _install(
    monkeypatch: pytest.MonkeyPatch, client: _FakeClient, boosted: list[dict[str, Any]]
) -> None:
    async def fake_boosts(url: str, timeout: int = 15) -> list[dict[str, Any]]:
        return boosted

    monkeypatch.setattr(tr, "http_get_any", fake_boosts)
    monkeypatch.setattr(tr, "get_shared_client", lambda t=15: client)


async def test_dexscreener_chain_filter_and_quality_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    client.add("GOODADDR")
    client.add("SMOLADDR", name="smol coin")  # garbage pattern → dropped
    client.add("POORADDR", liq=100.0)  # <$1000 liquidity → dropped
    client.add("QUIETADDR", vol=100.0)  # <$5000 volume → dropped
    boosted = [
        {"tokenAddress": "GOODADDR", "chainId": "solana"},
        {"tokenAddress": "SMOLADDR", "chainId": "solana"},
        {"tokenAddress": "POORADDR", "chainId": "solana"},
        {"tokenAddress": "QUIETADDR", "chainId": "solana"},
        {"tokenAddress": "ETHADDR", "chainId": "ethereum"},  # wrong chain → dropped
        {"tokenAddress": "", "chainId": "solana"},  # no address → dropped
    ]
    _install(monkeypatch, client, boosted)
    out = await tr._fetch_dexscreener_trending(chain="solana", limit=10)
    assert out is not None
    assert [(t["symbol"], t["liquidity_usd"]) for t in out] == [("GOOD", 50_000.0)]
    assert out[0]["source"] == "dexscreener"
    assert out[0]["change_24h"] == 5.0


async def test_dexscreener_non_list_body_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_boosts(url: str, timeout: int = 15) -> Any:
        return {"error": "down"}

    monkeypatch.setattr(tr, "http_get_any", fake_boosts)
    assert await tr._fetch_dexscreener_trending(chain="solana") is None
