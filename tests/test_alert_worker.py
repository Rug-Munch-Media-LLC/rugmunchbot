"""alert_worker — bot:alerts:queue payload contract tests.

Covers the Redis queue contract documented in alert_worker.py: JSON items
{type, user_id, event_id, payload, created_at} pushed by the backend
telegram_bridge. Malformed items must be logged and skipped — never crash
the worker tick (a crash kills alert delivery for every subscriber).
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any
from unittest.mock import AsyncMock

import pytest

from rugmunchbot import alert_worker, states


class FakeRedis:
    """Minimal async stand-in for the redis client used by the worker."""

    def __init__(self, items: list[str] | None = None) -> None:
        self.queue: deque[str] = deque(items or [])
        self.pushed: list[tuple[str, str]] = []
        self.rpop = AsyncMock(side_effect=self._rpop)
        self.lpush = AsyncMock(side_effect=self._lpush)

    async def _rpop(self, key: str) -> str | None:
        return self.queue.pop() if self.queue else None

    async def _lpush(self, key: str, value: str) -> None:
        self.pushed.append((key, value))
        self.queue.appendleft(value)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # Per-user rate limiter + dedupe set are module-global; isolate each test.
    states._alert_last_sent.clear()
    alert_worker._RECENT_EVENT_IDS.clear()
    yield
    states._alert_last_sent.clear()
    alert_worker._RECENT_EVENT_IDS.clear()


def _ctx(bot_send: AsyncMock | None = None) -> Any:
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.bot = _Ctx()
    ctx.bot.send_message = bot_send or AsyncMock()
    return ctx


def _item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "token.whale_move",
        "user_id": 42,
        "event_id": "evt-1",
        "payload": {
            "token_address": "0xabc",
            "token_symbol": "RUG",
            "chain": "ethereum",
            "value_decimal": 12000,
        },
        "created_at": "2026-08-21T00:00:00Z",
    }
    base.update(over)
    return base


# ── pop_alert: queue payload parsing ─────────────────────────────────────


@pytest.mark.asyncio
async def test_pop_alert_parses_valid_item(monkeypatch: pytest.MonkeyPatch) -> None:
    item = _item()
    fake = FakeRedis([json.dumps(item)])
    monkeypatch.setattr(alert_worker, "get_redis_async", lambda: fake)
    got = await alert_worker.pop_alert()
    assert got == item


@pytest.mark.asyncio
async def test_pop_alert_malformed_json_logs_and_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRedis(["{not-json!!"])
    monkeypatch.setattr(alert_worker, "get_redis_async", lambda: fake)
    # Must not raise — a poison message cannot kill the tick.
    assert await alert_worker.pop_alert() is None


@pytest.mark.asyncio
async def test_pop_alert_non_dict_json_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis([json.dumps([1, 2, 3]), json.dumps("a string")])
    monkeypatch.setattr(alert_worker, "get_redis_async", lambda: fake)
    assert await alert_worker.pop_alert() is None
    assert await alert_worker.pop_alert() is None


@pytest.mark.asyncio
async def test_pop_alert_empty_queue_and_missing_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(alert_worker, "get_redis_async", lambda: FakeRedis([]))
    assert await alert_worker.pop_alert() is None
    monkeypatch.setattr(alert_worker, "get_redis_async", lambda: None)
    assert await alert_worker.pop_alert() is None


# ── _format_alert: payload shapes ────────────────────────────────────────


def test_format_alert_whale_move() -> None:
    text = alert_worker._format_alert(_item())
    assert "Whale Move" in text
    assert "RUG" in text
    assert "0xabc" in text
    assert "/scan <code>0xabc</code>" in text  # actionable scan deep-link


def test_format_alert_price_change_directions() -> None:
    up = _item(
        type="token.price_change",
        payload={
            "token_address": "0xabc",
            "token_symbol": "rug",
            "chain": "solana",
            "direction": "up",
            "change_pct": "12.5",
            "price": 0.5,
        },
    )
    down = _item(
        type="token.price_change",
        event_id="evt-2",
        payload={
            "token_address": "0xabc",
            "token_symbol": "rug",
            "chain": "solana",
            "direction": "down",
            "change_pct": "-9",
            "price": 0.1,
        },
    )
    assert "\U0001f4c8" in alert_worker._format_alert(up)
    assert "\U0001f4c9" in alert_worker._format_alert(down)


@pytest.mark.parametrize(
    "etype",
    [
        "token.liquidity_change",
        "token.deployed",
        "token.first_swap",
        "wallet.activity",
        "token.monitor_change",
    ],
)
def test_format_alert_known_types_render(etype: str) -> None:
    text = alert_worker._format_alert(_item(type=etype, event_id=f"evt-{etype}"))
    assert isinstance(text, str)
    assert len(text) > 20


def test_format_alert_unknown_type_falls_back() -> None:
    text = alert_worker._format_alert(_item(type="something.brand.new", event_id="evt-x"))
    assert "Alert:" in text


def test_format_alert_garbage_payload_does_not_crash() -> None:
    # payload may be any provider junk: non-dict, missing keys, bad numbers.
    text = alert_worker._format_alert(_item(payload="not a dict", event_id="evt-y"))
    assert isinstance(text, str)
    text2 = alert_worker._format_alert(_item(payload={"value_decimal": "junk"}, event_id="evt-z"))
    assert "$0" in text2  # _fmt_money falls back instead of raising


# ── _deliver_one: invalid items are skipped loudly, never crash ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_user", [None, "42", 0, -5])
async def test_deliver_one_invalid_user_id_skips_without_sending(bad_user: Any) -> None:
    send = AsyncMock()
    ok = await alert_worker._deliver_one(
        _ctx(send), _item(user_id=bad_user, event_id=f"evt-bad-{bad_user}")
    )
    assert ok is True  # skipped intentionally — no retry loop
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_one_sends_formatted_html_to_user() -> None:
    send = AsyncMock()
    ok = await alert_worker._deliver_one(_ctx(send), _item())
    assert ok is True
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["parse_mode"] == "HTML"
    assert "Whale Move" in kwargs["text"]


@pytest.mark.asyncio
async def test_deliver_one_dedupes_repeated_event_id() -> None:
    send = AsyncMock()
    assert await alert_worker._deliver_one(_ctx(send), _item()) is True
    # Same event_id again (overlapping subscriptions) → skipped.
    assert await alert_worker._deliver_one(_ctx(send), _item()) is True
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_deliver_one_rate_limited_user_skipped() -> None:
    states._alert_last_sent[42] = 9_999_999_999.0  # recently served
    send = AsyncMock()
    ok = await alert_worker._deliver_one(_ctx(send), _item(event_id="evt-rl"))
    assert ok is True
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_one_send_failure_requests_retry() -> None:
    send = AsyncMock(side_effect=RuntimeError("telegram down"))
    ok = await alert_worker._deliver_one(_ctx(send), _item(event_id="evt-fail"))
    assert ok is False  # signals re-queue


# ── retry / dead-letter plumbing ─────────────────────────────────────────


def test_item_identity_stable_across_retry_metadata() -> None:
    item = _item()
    retried = {**item, "retry_count": 2, "last_error": "boom"}
    assert alert_worker._item_identity(item) == alert_worker._item_identity(retried)


@pytest.mark.asyncio
async def test_requeue_increments_retry_and_deadletters_at_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeRedis()
    monkeypatch.setattr(alert_worker, "get_redis_async", lambda: fake)

    item = _item(event_id="evt-retry")
    await alert_worker._requeue_or_deadletter(item)
    assert item["retry_count"] == 1
    assert fake.pushed[-1][0] == alert_worker.ALERT_QUEUE_KEY

    item["retry_count"] = alert_worker.MAX_ALERT_RETRIES - 1
    await alert_worker._requeue_or_deadletter(item)
    assert (
        fake.pushed[-1][0] == alert_worker.ALERT_DEAD_LETTER_KEY
    )  # budget exhausted → dead letter


@pytest.mark.asyncio
async def test_deliver_pending_alerts_drains_queue_and_requeues_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = _item(event_id="evt-ok", user_id=1)
    failing = _item(event_id="evt-nope", user_id=2)
    fake = FakeRedis([json.dumps(good), json.dumps(failing)])
    monkeypatch.setattr(alert_worker, "get_redis_async", lambda: fake)
    monkeypatch.setattr(alert_worker, "SEND_DELAY", 0)

    send = AsyncMock(
        side_effect=lambda chat_id, **kw: (
            (_ for _ in ()).throw(RuntimeError("down")) if chat_id == 2 else None
        )
    )
    processed = await alert_worker.deliver_pending_alerts(_ctx(send))
    assert processed == 2
    # The failed item came back with retry_count=1 (re-queued, not dropped).
    requeued = [json.loads(v) for k, v in fake.pushed if k == alert_worker.ALERT_QUEUE_KEY]
    assert any(r.get("event_id") == "evt-nope" and r.get("retry_count") == 1 for r in requeued)
