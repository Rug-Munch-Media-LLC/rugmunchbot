"""Inline-mode text builders (handlers/callback/_inline.py).

The builders take their fetch + formatters via DI, so these are pure output
tests with canned payloads — no network, no Telegram.
"""

from rugmunchbot.formatters import footer_links
from rugmunchbot.handlers.callback._inline import (
    _inline_market_overview,
    _inline_movers_text,
)
from rugmunchbot.handlers.market._render import _change_emoji, _fmt_usd


def _fetch_stub(payloads):
    async def f(path, params=None):
        return payloads.get(path)

    return f


async def test_market_overview_renders_all_sections():
    payloads = {
        "/api/v1/market/overview": {
            "total_market_cap": 3.2e12,
            "total_volume_24h": 120e9,
            "btc_dominance": "54.3",
            "eth_dominance": 18.2,
            "active_cryptocurrencies": "12000",
            "fear_greed": {"value": 78, "classification": "Extreme Greed"},
        },
        "/api/v1/market/macro": {
            "items": [
                {"asset": "BTC", "current_price": 67000, "change_24h": 1.5},
                {"asset": "ETH", "current_price": 3500, "change_24h": -0.5},
                {"asset": "XRP", "current_price": 1, "change_24h": 0},
            ]
        },
    }
    out = await _inline_market_overview(_fetch_stub(payloads), _fmt_usd)
    assert out is not None
    assert "Market Overview" in out
    assert "$3.20T" in out
    assert "54.3%" in out
    assert "18.2%" in out
    assert "Active Coins: <b>12,000</b>" in out
    assert "<b>78</b> (Extreme Greed)" in out
    assert "BTC:" in out
    assert "ETH:" in out
    assert "XRP" not in out  # macro renders the first two items only
    assert out.endswith(footer_links())


async def test_market_overview_none_without_payload():
    assert await _inline_market_overview(_fetch_stub({}), _fmt_usd) is None
    empty = {"/api/v1/market/overview": {}}
    assert await _inline_market_overview(_fetch_stub(empty), _fmt_usd) is None


async def test_movers_split_cap_and_empty_states():
    gainers = [
        {"symbol": f"g{i}", "name": f"G{i}", "change_24h": 5 + i, "price": 1, "volume_24h": 1000}
        for i in range(8)
    ]
    losers = [{"symbol": "l1", "name": "L1", "change_24h": -3, "price": 2, "volume_24h": 500}]
    out = await _inline_movers_text(
        _fetch_stub({"/api/v1/market/movers": {"items": gainers + losers}}),
        _fmt_usd,
        _change_emoji,
    )
    assert out is not None
    assert "Market Movers" in out
    assert "G5" in out  # the 6th gainer (g0..g5) renders
    assert "G6" not in out  # ...the 7th is capped out
    assert "L1" in out
    assert "No losers" not in out
    assert out.endswith(footer_links())

    only_gainers = await _inline_movers_text(
        _fetch_stub(
            {"/api/v1/market/movers": {"items": [{"symbol": "x", "name": "X", "change_24h": 1}]}}
        ),
        _fmt_usd,
        _change_emoji,
    )
    assert "No losers" in only_gainers

    assert await _inline_movers_text(_fetch_stub({}), _fmt_usd, _change_emoji) is None
    empty = {"/api/v1/market/movers": {"items": []}}
    assert await _inline_movers_text(_fetch_stub(empty), _fmt_usd, _change_emoji) is None
