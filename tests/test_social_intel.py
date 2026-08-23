"""Social intel aggregation tests (mocked transports)."""

import asyncio

from rugmunchbot.tools.social_intel import social_intel


def test_requires_symbol():
    r = asyncio.run(social_intel(symbol=""))
    assert not r.ok
    assert "symbol" in (r.data or {}).get("error", "")


def test_no_signals_degrades_honestly(monkeypatch):
    from rugmunchbot.tools import social_intel as m

    async def empty_feed(sym):
        return {}

    async def empty_buzz(sym):
        return {}

    monkeypatch.setattr(m, "_feed_sentiment", empty_feed)
    monkeypatch.setattr(m, "_buzz", empty_buzz)
    r = asyncio.run(m.social_intel(symbol="XYZ"))
    assert not r.ok
    assert "no social signals" in (r.data or {}).get("error", "")


def test_composite_narrative(monkeypatch):
    from rugmunchbot.tools import social_intel as m

    async def feed(sym):
        return {"articles": 5, "sentiment_lean": 32.0, "avg_bias": 0.2, "avg_factual": 0.8}

    async def buzz(sym):
        return {"mentions_24h": 40, "unique_domains": 12}

    monkeypatch.setattr(m, "_feed_sentiment", feed)
    monkeypatch.setattr(m, "_buzz", buzz)
    r = asyncio.run(m.social_intel(symbol="BTC"))
    assert r.ok
    d = r.data
    assert d["mention_velocity"] == "high"
    assert "bullish" in d["narrative"]
