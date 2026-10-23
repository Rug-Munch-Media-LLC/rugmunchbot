"""Card rendering, the swap seam and config persistence — offline."""

from __future__ import annotations

import asyncio
import json

import pytest

from rugmunchbot.buybot.execution import BuyExecutor, BuyRequest, ExecutionResult
from rugmunchbot.buybot.models import BuybotConfig
from rugmunchbot.buybot.render import BuyEvent, MarketSnapshot, button_rows, render_card
from rugmunchbot.buybot.store import load_config, save_config
from rugmunchbot.community.models import Chain, TokenConfig

MINT = "So11111111111111111111111111111111111111112"


class FakeRedis:
    def __init__(self, document: dict | None = None, *, broken: bool = False) -> None:
        self.document = document
        self.broken = broken
        self.writes: list[tuple[str, str]] = []

    async def get(self, key: str):
        if self.broken:
            raise RuntimeError("redis down")
        return json.dumps(self.document) if self.document is not None else None

    async def set(self, key: str, value: str) -> None:
        if self.broken:
            raise RuntimeError("redis down")
        self.writes.append((key, value))
        self.document = json.loads(value)


class FakeSwap:
    def __init__(self, tx: str = "0xdeadbeefcafe") -> None:
        self.tx = tx
        self.requests: list[BuyRequest] = []

    async def buy(self, request: BuyRequest) -> str:
        self.requests.append(request)
        return self.tx


def _config(**overrides) -> BuybotConfig:
    token = TokenConfig(address=MINT, symbol="BONK", name="Bonk", chain=Chain.SOLANA)
    return BuybotConfig(chat_id=-100, token=token, **overrides)


def test_card_escapes_dynamic_values_and_keeps_row_order() -> None:
    config = _config()
    card = render_card(
        config,
        BuyEvent(buyer="<script>alert(1)</script>", amount_tokens=1200, usd_value=42.5),
        note="<b>not bold</b>",
    )
    assert "&lt;script&gt;" in card
    assert "<script>" not in card
    assert "&lt;b&gt;not bold&lt;/b&gt;" in card
    assert card.index("Amount:") < card.index("Value:")
    assert "n/a" in card  # no market source => honest unknown, never zero


def test_card_is_bounded_and_shows_market_rows() -> None:
    market = MarketSnapshot(
        price_usd=0.00001234, mcap_usd=1_500_000, liquidity_usd=250_000, risk_score=72
    )
    card = render_card(_config(), BuyEvent("alice", 250.0, 1000.0), market)
    assert len(card) < 4096
    assert "$1.50M" in card
    assert "$250.00K" in card
    assert "high" in card
    assert "preview" not in card


@pytest.mark.parametrize(
    ("score", "badge"),
    [(None, "unrated"), (0, "low"), (30, "low"), (31, "medium"), (61, "high"), (81, "avoid")],
)
def test_risk_badge_boundaries(score, badge) -> None:
    market = MarketSnapshot(risk_score=score)
    assert badge in render_card(_config(), BuyEvent("bob", 25.0, 1.0), market)


def test_button_rows_are_per_preset_and_fit_callback_limits() -> None:
    config = _config(amounts={"buy": [0.1, 0.5, 1.0], "sell": []})
    rows = button_rows(config, -100)
    flat = [item for row in rows for item in row]
    buy = [item for item in flat if item[1].startswith("buy|")]
    assert len(buy) == 3
    assert all(len(data.encode()) <= 64 for _, data in flat)
    assert buy[0][1] == f"buy|-100|{MINT[:12]}|0.1"


def test_executor_previews_when_unconfigured() -> None:
    executor = BuyExecutor()
    assert executor.configured is False
    result = asyncio.run(executor.buy(BuyRequest(-100, 7, "solana", MINT, 0.5, "k")))
    assert result.status == "preview"
    assert result.ok is False
    assert "not configured" in result.reason


def test_executor_submits_only_a_real_tx() -> None:
    swap = FakeSwap()
    result = asyncio.run(BuyExecutor(swap).buy(BuyRequest(-100, 7, "solana", MINT, 0.5, "k1")))
    assert result == ExecutionResult("submitted", "submitted", "0xdeadbeefcafe")
    assert result.ok is True
    assert swap.requests[0].idempotency_key == "k1"
    empty = asyncio.run(BuyExecutor(FakeSwap("")).buy(BuyRequest(-100, 7, "s", MINT, 1, "k2")))
    assert empty.status == "failed"


def test_executor_reports_adapter_failure_without_swallowing_it() -> None:
    class Boom:
        async def buy(self, request: BuyRequest) -> str:
            raise ValueError("insufficient funds")

    result = asyncio.run(BuyExecutor(Boom()).buy(BuyRequest(-100, 7, "s", MINT, 1, "k")))
    assert result.status == "failed"
    assert "ValueError" in result.reason


def test_store_roundtrip_and_fail_soft_reads() -> None:
    redis = FakeRedis()
    ok, reason = asyncio.run(save_config(_config(enabled=True), redis))
    assert ok is True
    assert reason is None
    stored = json.loads(redis.writes[0][1])
    assert stored["token"]["address"] == MINT
    loaded = asyncio.run(load_config(-100, redis))
    assert loaded.enabled is True
    assert loaded.token is not None
    assert loaded.token.symbol == "BONK"
    assert (
        asyncio.run(load_config(-100, FakeRedis({"chat_id": -100, "enabled": "yes"}))).enabled
        is False
    )
    assert asyncio.run(load_config(-100, None)).enabled is False
    ok, reason = asyncio.run(save_config(_config(), FakeRedis(broken=True)))
    assert ok is False
    assert reason
