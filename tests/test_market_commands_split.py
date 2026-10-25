"""market command package — split contract + observable command behaviour.

Covers the parts of the 2026-10-25 split that can break silently: facade
imports, ``register_market`` ordering (part of the router contract), the
static /rugcheck renderer, and the data-driven handlers through a mocked
HTTP transport (respx) — no monkeypatching.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from rugmunchbot.config import BACKEND_URL
from rugmunchbot.handlers import market
from rugmunchbot.handlers.market import _commands, _constants
from rugmunchbot.handlers.market import _cmd_market, _cmd_movers, _cmd_news

# ── fakes ───────────────────────────────────────────────────────────────────


class FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.kwargs: list[dict] = []

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)
        self.kwargs.append(kwargs)
        return self

    async def edit_text(self, text, **kwargs):
        self.texts.append(text)
        self.kwargs.append(kwargs)
        return self


class FakeApp:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def add_handler(self, handler) -> None:
        self.commands.append(next(iter(handler.commands)))


def _update(user_id: int = 7):
    return SimpleNamespace(message=FakeMessage(), effective_user=SimpleNamespace(id=user_id))


# ── facade contract ─────────────────────────────────────────────────────────


def test_facade_and_package_expose_the_same_objects():
    for name in _commands.__all__:
        assert getattr(market, name) is getattr(_commands, name), name


def test_market_unavailable_constant_is_shared():
    assert market.MARKET_UNAVAILABLE is _constants.MARKET_UNAVAILABLE
    assert "Market Data Unavailable" in market.MARKET_UNAVAILABLE


def test_register_market_keeps_handler_order():
    """Invariant: registration order is market, trending, movers, launches."""
    app = FakeApp()
    market.register_market(app)
    assert app.commands == ["market", "trending", "movers", "launches"]


# ── /rugcheck (static, no I/O) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_rugcheck_renders_checklist():
    update = _update()
    await market.cmd_rugcheck(update, SimpleNamespace(args=[]))
    text = update.message.texts[0]
    assert "Rug Pull Checklist" in text
    assert "Honeypot detected" in text
    assert update.message.kwargs[0]["reply_markup"] is not None


# ── /market ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_cmd_market_renders_overview():
    respx.get(f"{BACKEND_URL}/api/v1/market/overview").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_market_cap": 2_400_000_000_000,
                "total_volume_24h": 90_000_000_000,
                "btc_dominance": 54.2,
                "fear_greed": {"value": 72, "classification": "Greed"},
            },
        )
    )
    respx.get(f"{BACKEND_URL}/api/v1/market/macro").mock(
        return_value=httpx.Response(200, json={"items": [{"asset": "BTC", "current_price": 64000}]})
    )
    update = _update()
    await market.cmd_market(update, SimpleNamespace(args=[]))
    text = update.message.texts[-1]
    assert "Market Overview" in text
    assert "BTC Dominance" in text
    assert "Fear &amp; Greed" in text or "Fear & Greed" in text


@pytest.mark.asyncio
@respx.mock
async def test_cmd_market_unavailable_path():
    respx.get(f"{BACKEND_URL}/api/v1/market/overview").mock(return_value=httpx.Response(503))
    respx.get(f"{BACKEND_URL}/api/v1/market/macro").mock(return_value=httpx.Response(503))
    update = _update()
    await market.cmd_market(update, SimpleNamespace(args=[]))
    assert update.message.texts[-1] == _constants.MARKET_UNAVAILABLE


# ── /movers ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_cmd_movers_unknown_timeframe_defaults_to_24h():
    respx.get(f"{BACKEND_URL}/api/v1/market/movers").mock(
        return_value=httpx.Response(
            200,
            json={
                "top_gainers": [{"symbol": "A", "address": "", "change_24h": 12.5}],
                "top_losers": [],
                "sources": {},
            },
        )
    )
    update = _update()
    await market.cmd_movers(update, SimpleNamespace(args=["nonsense"]))
    assert "24H" in update.message.texts[0]


@pytest.mark.asyncio
@respx.mock
async def test_cmd_movers_unavailable_path():
    respx.get(f"{BACKEND_URL}/api/v1/market/movers").mock(return_value=httpx.Response(500))
    update = _update()
    await market.cmd_movers(update, SimpleNamespace(args=[]))
    assert update.message.texts[-1] == _constants.MARKET_UNAVAILABLE


# ── /launches ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_cmd_launches_empty_items_is_unavailable():
    respx.get(f"{BACKEND_URL}/api/v1/market/launches").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    update = _update()
    await market.cmd_launches(update, SimpleNamespace(args=[]))
    assert update.message.texts[-1] == _constants.MARKET_UNAVAILABLE


@pytest.mark.asyncio
@respx.mock
async def test_cmd_launches_renders_items_with_refresh_and_menu():
    respx.get(f"{BACKEND_URL}/api/v1/market/launches").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "symbol": "newtok",
                        "name": "New Token",
                        "address": "So11111111111111111111111111111111111111112",
                        "chain": "solana",
                        "change_24h": 44.0,
                        "bundler_detected": True,
                    }
                ]
            },
        )
    )
    update = _update()
    await market.cmd_launches(update, SimpleNamespace(args=[]))
    text = update.message.texts[-1]
    assert "NEWTOK" in text and "New Token" in text
    callbacks = [
        b.callback_data
        for row in update.message.kwargs[-1]["reply_markup"].inline_keyboard
        for b in row
        if b.callback_data
    ]
    assert "launches_refresh" in callbacks and "menu_main" in callbacks


# ── module identity (one command per module) ────────────────────────────────


def test_each_command_lives_in_its_own_module():
    assert market.cmd_market.__module__ == _cmd_market.__name__
    assert market.cmd_movers.__module__ == _cmd_movers.__name__
    assert market.cmd_news.__module__ == _cmd_news.__name__
    modules = {
        market.cmd_launches.__module__,
        market.cmd_market.__module__,
        market.cmd_movers.__module__,
        market.cmd_news.__module__,
        market.cmd_rugcheck.__module__,
        market.cmd_trending.__module__,
        market.cmd_whales.__module__,
    }
    assert len(modules) == 7


# ── /news (fallback chain) ──────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_cmd_news_prefers_internal_feed():
    respx.get(f"{BACKEND_URL}/api/v1/internal/news-feed").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "ETF approved",
                        "source": "Wire",
                        "url": "https://n.example/1",
                        "bias": "center",
                        "sentiment_score": 40,
                        "factual_rating": "high",
                        "source_tier": 1,
                    }
                ]
            },
        )
    )
    update = _update()
    await market.cmd_news(update, SimpleNamespace(args=[]))
    text = update.message.texts[-1]
    assert "Crypto News" in text
    assert "RMI News Network" in text
    assert "ETF approved" in text


@pytest.mark.asyncio
@respx.mock
async def test_cmd_news_falls_back_to_backend_trending():
    respx.get(f"{BACKEND_URL}/api/v1/internal/news-feed").mock(return_value=httpx.Response(503))
    respx.get(f"{BACKEND_URL}/api/v1/news/trending").mock(
        return_value=httpx.Response(200, json={"items": [{"title": "Trend story", "url": ""}]})
    )
    update = _update()
    await market.cmd_news(update, SimpleNamespace(args=[]))
    text = update.message.texts[-1]
    assert "via Trending" in text
    assert "Trend story" in text


@pytest.mark.asyncio
@respx.mock
async def test_cmd_news_all_sources_down_is_a_graceful_message():
    respx.route().mock(return_value=httpx.Response(503))
    update = _update()
    await market.cmd_news(update, SimpleNamespace(args=[]))
    text = update.message.texts[-1]
    assert "News feed temporarily unavailable" in text
    assert "@rmialerts" in text


# ── /whales + /trending failure paths ──────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_cmd_whales_empty_items_is_unavailable():
    respx.get(f"{BACKEND_URL}/api/v1/market/whales").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    update = _update()
    await market.cmd_whales(update, SimpleNamespace(args=[]))
    assert update.message.texts[-1] == _constants.MARKET_UNAVAILABLE


@pytest.mark.asyncio
@respx.mock
async def test_cmd_trending_unavailable_path():
    respx.route().mock(return_value=httpx.Response(503))
    update = _update()
    await market.cmd_trending(update, SimpleNamespace(args=[]))
    assert "Trending Unavailable" in update.message.texts[-1]
