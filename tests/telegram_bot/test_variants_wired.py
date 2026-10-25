"""Wiring tests: render_variant is live in the 5 hot handlers; numbers never vary."""

from __future__ import annotations

import inspect

import pytest

from rugmunchbot.bot.variants import POOLS, render_variant
from rugmunchbot.handlers import portfolio
from rugmunchbot.handlers.market import _cmd_market, _cmd_rugcheck, _cmd_trending, _cmd_whales

NEW = ("market.loading", "market.empty", "portfolio.empty_portfolio", "rugcheck.footer")
WIRED = {
    "trending": (_cmd_trending, ("market.loading",)),
    "whales": (_cmd_whales, ("market.loading",)),
    "rugcheck": (_cmd_rugcheck, ("rugcheck.footer",)),
    "market": (_cmd_market, ("market.loading",)),
    "portfolio": (portfolio, ("market.loading", "portfolio.empty_portfolio")),
}
FIELDS = {"subject": "whale flow (24h)"}


@pytest.mark.parametrize("situation", NEW)
def test_pool_has_three_phrasings(situation):
    assert len(POOLS[situation]) == 3


@pytest.mark.parametrize("situation", NEW)
def test_same_ids_same_text(situation):
    a = render_variant(situation, chat_id=9, message_id=4, **FIELDS)
    b = render_variant(situation, chat_id=9, message_id=4, **FIELDS)
    assert a == b


@pytest.mark.parametrize("situation", NEW)
def test_different_ids_reach_two_phrasings(situation):
    outs = {render_variant(situation, chat_id=9, message_id=m, **FIELDS) for m in range(8)}
    assert len(outs) >= 2


def test_whale_label_keeps_24h():
    for m in range(8):
        assert "24h" in render_variant("market.loading", 1, m, subject="whale flow (24h)")


@pytest.mark.parametrize("name", sorted(WIRED))
def test_handler_is_wired(name):
    module, situations = WIRED[name]
    src = inspect.getsource(module)
    assert "render_variant(" in src
    for situation in situations:
        assert situation in src
    assert "as an ai" not in src.lower()


def test_numeric_literals_untouched():
    assert "10 tokens per audit" in inspect.getsource(portfolio)
    assert "{len(entries)} tokens" in inspect.getsource(portfolio)
    assert "len(entries)" in inspect.getsource(portfolio)
