"""Wave 10 — daily_market_summary._assemble: context serialization + gather.

All expectations probed against the implementation before asserting (oracle
policy). ``_serialize_context`` is pure; ``gather_summary_data`` is tested with
monkeypatched fetchers (it must never raise and must degrade per-block).
"""

from __future__ import annotations

import pytest

import rugmunchbot.channels.daily_market_summary._assemble as asm
from rugmunchbot.channels.daily_market_summary._assemble import (
    _serialize_context,
    gather_summary_data,
)

# ── _serialize_context (pure) ───────────────────────────────────────────


def test_serialize_full_context_sections() -> None:
    ctx = {
        "prices": {
            "btc_price": 67000.0,
            "btc_change_24h": 1.5,
            "eth_price": 3500,
            "eth_change_24h": None,
            "sol_price": None,
            "sol_change_24h": None,
        },
        "condition": {
            "verdict": "RISK-ON",
            "confidence": "medium",
            "reasons": ["breadth positive", "funding normal"],
            "signals": {"fear_greed": {"value": 72, "classification": "Greed"}},
        },
        "global_metrics": {
            "total_mcap": 2_400_000_000_000,
            "total_volume_24h": 90e9,
            "btc_dominance": 54.32,
            "eth_dominance": 17.11,
            "mcap_change_24h": -0.4,
        },
        "movers": {
            "gainers": [
                {"symbol": "bonk", "change_24h": 22.5, "volume_24h": 1e6, "market_cap": 2e9}
            ],
            "losers": [],
        },
        "trending": [
            {
                "symbol": "WIF",
                "change_24h": 9.1,
                "volume_24h": 5e6,
                "market_cap": 3e9,
                "chain": "solana",
            }
        ],
        "news": [{"content": "ETF inflows hit record.", "canonical_url": "https://x.com/n"}],
    }
    out = _serialize_context(ctx)
    assert "MAJOR PRICES (24h): BTC $67,000 (+1.5%) · ETH $3,500 (N/A) · SOL N/A (N/A)" in out
    assert "FEAR & GREED: 72/100 (Greed)" in out
    assert "MARKET VERDICT: RISK-ON (confidence medium)" in out
    assert "VERDICT REASONS: breadth positive | funding normal" in out
    assert (
        "GLOBAL METRICS: total mcap $2400.0B · 24h volume $90.0B · BTC dominance 54.3% · ETH dominance 17.1% · mcap 24h -0.4%"
        in out
    )
    assert "TOP MOVERS (24h): GAINERS: BONK +22.5%" in out
    assert "TRENDING TOKENS: WIF +9.1%" in out
    assert "(chain solana)" in out
    assert "TOP NEWS (RAG news_v2): ETF inflows hit record. [url=https://x.com/n]" in out


def test_serialize_empty_context_is_empty_string() -> None:
    assert _serialize_context({}) == ""


def test_serialize_movers_timeframe_fallback() -> None:
    out = _serialize_context(
        {"movers": {"losers": [{"name": "pepe", "timeframe": "6h", "change_6h": -8.0}]}}
    )
    assert "LOSERS: PEPE -8.0%" in out


def test_serialize_narrative_and_kol_blocks() -> None:
    out = _serialize_context(
        {
            "narratives": [
                {
                    "query": "$SOL ETF",
                    "title": "Solana ETF odds rise",
                    "url": "https://x.com/s",
                    "snippet": "Analysts raise odds.",
                }
            ],
            "kol_signals": [
                {
                    "query": "$BTC",
                    "title": "Whale thread",
                    "url": "https://x.com/k",
                    "page_preview": "Thread preview text.",
                }
            ],
        }
    )
    assert (
        'NARRATIVE SIGNALS (web search): [$SOL ETF] "Solana ETF odds rise" (https://x.com/s) Analysts raise odds.'
        in out
    )
    assert (
        'X/KOL SIGNALS (web search): [$BTC] "Whale thread" (https://x.com/k) Thread preview text.'
        in out
    )


# ── gather_summary_data ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_web_signals(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(asm, "DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED", False)


async def test_gather_collects_all_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok_prices():
        return {"btc_price": 1.0}

    async def ok_condition():
        return {"verdict": "NEUTRAL"}

    async def ok_trending():
        return [{"symbol": "WIF"}]

    async def ok_movers():
        return {"gainers": [], "losers": []}

    async def ok_news():
        return [{"title": "n"}]

    async def ok_metrics():
        return {"total_mcap": 1.0}

    for name, fn in [
        ("_fetch_major_prices", ok_prices),
        ("_fetch_market_condition", ok_condition),
        ("_fetch_trending", ok_trending),
        ("_fetch_movers", ok_movers),
        ("_fetch_news", ok_news),
        ("_fetch_global_metrics", ok_metrics),
    ]:
        monkeypatch.setattr(asm, name, fn)
    data = await gather_summary_data()
    assert set(data["sources"]) == {
        "prices",
        "condition",
        "trending",
        "movers",
        "news",
        "global_metrics",
    }
    assert data["prices"] == {"btc_price": 1.0}
    assert data["generated_at"]  # ISO stamp present


async def test_gather_never_raises_on_fetcher_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom():
        raise ConnectionError("source down")

    async def empty():
        return {}

    monkeypatch.setattr(asm, "_fetch_major_prices", boom)
    monkeypatch.setattr(asm, "_fetch_market_condition", empty)
    monkeypatch.setattr(asm, "_fetch_trending", boom)
    monkeypatch.setattr(asm, "_fetch_movers", empty)
    monkeypatch.setattr(asm, "_fetch_news", boom)
    monkeypatch.setattr(asm, "_fetch_global_metrics", empty)
    data = await gather_summary_data()  # must not raise
    assert data["prices"] == {}
    assert data["trending"] == []
    assert data["news"] == []
    # Only blocks with TRUTHY values count as active sources — {} is falsy.
    assert set(data["sources"]) == set()
