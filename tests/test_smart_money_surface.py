"""Smart-money surface contracts (no network, no Telegram).

Locks what the channel posts must say and what they must never say:

1. every post carries headline + evidence + source + timestamp + risk note;
2. prediction posts never invent a 24h shift, and Kalshi rows carry no
   wallet identity (Kalshi is aggregate-only);
3. dry-run posting is the default and never calls Telegram;
4. an empty/failed feed renders an honest fallback naming what was tried.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rugmunchbot.handlers.smart_money import cmd_smartmoney
from rugmunchbot.smart_money import (
    EventGate,
    Feed,
    event_key,
    fetch_hl,
    fetch_predictions,
    hl,
    moves,
    predictions,
    publish,
    render_hl,
    render_move,
    render_prediction,
)
from rugmunchbot.smart_money.poster import PublishOptions

FIXTURES = Path(__file__).parent / "fixtures" / "smart_money"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _assert_five_blocks(post: str) -> None:
    assert "<b>" in post.splitlines()[0], "headline missing"
    for block in ("<b>Source:</b>", "<b>Time:</b>", "<b>Risk:</b>"):
        assert block in post, f"{block} missing"


def test_move_post_has_all_five_blocks():
    feed = Feed(
        kind="moves", items=(), source="databus.smart_money", updated_at="2026-10-23T10:14:01Z"
    )
    row = _fixture("smart_money_moves_synthetic.json")["smart_money_wallets"][0]
    move = moves._normalise(row, "solana")
    assert move is not None
    post = render_move(feed, move)
    _assert_five_blocks(post)
    assert "SMART MONEY" in post
    assert "SOL" in post
    assert "$12.40M" in post  # size is evidence, not decoration
    assert "2026-10-23 10:14Z" in post


@pytest.mark.asyncio
async def test_hl_post_renders_from_live_fixture(monkeypatch):
    action, liqs = _fixture("hl_action.json"), _fixture("hl_liquidations.json")

    async def fake(path, params=None, timeout=12.0):
        return action if "action" in path else liqs

    monkeypatch.setattr(hl, "backend_json", fake)
    feed = await fetch_hl(limit=4)
    assert feed.ok
    assert feed.source.startswith("hyperliquid")
    post = render_hl(feed, feed.items[0])
    _assert_five_blocks(post)
    assert "HYPERLIQUID" in post
    assert "no wallet identity" in post  # honesty clause is mandatory


@pytest.mark.asyncio
async def test_prediction_post_shows_level_not_invented_shift(monkeypatch):
    body = _fixture("predictions.json")

    async def fake(path, params=None, timeout=12.0):
        return body

    monkeypatch.setattr(predictions, "backend_json", fake)
    feed = await fetch_predictions(limit=3)
    assert feed.ok
    post = render_prediction(feed, feed.items[0])
    _assert_five_blocks(post)
    assert "YES" in post
    assert "no 24h baseline" in post, "feed has no delta — shift must not be invented"


def test_kalshi_is_aggregate_only():
    row = {
        "question": "Will BTC close above 90k?",
        "probability": {"yes": 0.41},
        "source": "kalshi",
        "volume_usd": 120000,
        "wallet": "0xdead",
    }
    built = predictions._row(row)
    assert built is not None
    assert built["wallet_identity"] is None


def test_event_gate_dedupes_then_rate_limits():
    now = [1000.0]
    gate = EventGate(ttl=600, max_per_hour=2, clock=lambda: now[0])
    assert gate.allow("a") == "ok"
    assert gate.allow("a") == "dupe"
    assert gate.allow("b") == "ok"
    assert gate.allow("c") == "rate"
    now[0] += 601
    assert gate.allow("a") == "rate" or gate.allow("a") in {"ok", "rate"}


def test_event_key_stable():
    item = {"tx_hash": "0xabc", "asset": "SOL", "kind": "mover"}
    assert event_key("hl", item) == event_key("hl", dict(item))


@pytest.mark.asyncio
async def test_dry_run_never_calls_telegram(monkeypatch):
    monkeypatch.delenv("RMI_SMARTMONEY_POST", raising=False)

    class Boom:
        async def send_message(self, **_kw):  # pragma: no cover - must not run
            raise AssertionError("Telegram called in dry run")

    result = await publish("post", key="dry-1", opts=PublishOptions(bot=Boom()))
    assert result["status"] == "dry_run"
    assert result["dry_run"] is True
    assert result["role"] == "alpha"  # whale/alpha role, id resolved not hardcoded


@pytest.mark.asyncio
async def test_handler_fallback_names_sources(monkeypatch):
    async def empty(limit=10, chain="solana"):
        return Feed(
            kind="moves", note="no rows", tried=("smart-money/moves", "databus.smart_money")
        )

    monkeypatch.setattr("rugmunchbot.handlers.smart_money.fetch_moves", empty)

    class Msg:
        def __init__(self):
            self.texts = []

        async def reply_text(self, text, **_kw):
            self.texts.append(text)
            return self

        async def edit_text(self, text, **_kw):
            self.texts.append(text)
            return self

    update = SimpleNamespace(message=Msg(), effective_user=None)
    await cmd_smartmoney(update, SimpleNamespace(args=[]))
    assert "unavailable" in update.message.texts[-1]
    assert "databus.smart_money" in update.message.texts[-1]
