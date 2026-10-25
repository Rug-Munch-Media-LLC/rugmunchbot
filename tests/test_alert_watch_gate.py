"""The watch gate as ``alert_worker`` uses it: tier, cap, quiet-hours deferral.

No patching: the gate's tier lookup and clock are module seams
(``watch_gate.configure``), so these assertions drive the real worker path and
the real policy object.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from rugmunchbot import alert_worker, states
from rugmunchbot.premium import watch_gate
from rugmunchbot.premium.watch_alerts import QuietHours

if TYPE_CHECKING:
    from collections.abc import Iterator

USER = 770001
ALWAYS_QUIET = QuietHours(start=0, end=24)
NO_QUIET = QuietHours(start=0, end=0)


@pytest.fixture(autouse=True)
def _seams() -> Iterator[None]:
    """Pure tier lookup, quiet hours off; production defaults restored after."""
    watch_gate.configure(read_tier=lambda _uid: "free", quiet=NO_QUIET)
    alert_worker._RECENT_EVENT_IDS.clear()
    states._alert_last_sent.clear()
    yield
    watch_gate.configure()
    alert_worker._RECENT_EVENT_IDS.clear()
    states._alert_last_sent.clear()


def _ctx(send: AsyncMock) -> Any:
    return SimpleNamespace(bot=SimpleNamespace(send_message=send))


def _item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "token.monitor_change",
        "user_id": USER,
        "event_id": "evt-watch-1",
        "payload": {
            "token_address": "0xabc",
            "token_symbol": "RUG",
            "chain": "solana",
            "changes": ["Liquidity lock removed"],
        },
        "created_at": "2026-10-25T00:00:00Z",
    }
    base.update(over)
    return base


def test_cap_follows_the_tier_and_unknown_tiers_floor_to_free() -> None:
    assert watch_gate.tier_for(USER) == "free"
    assert watch_gate.gate_for("free").cap == 5
    assert watch_gate.gate_for("scout").cap == 20
    assert watch_gate.gate_for("nonsense").cap == 5


def test_a_failing_tier_lookup_floors_to_free() -> None:
    def boom(_uid: int) -> str:
        raise RuntimeError("db down")

    watch_gate.configure(read_tier=boom)
    assert watch_gate.tier_for(USER) == "free", "a DB hiccup must not lift a cap"


def test_gate_holds_in_quiet_hours_and_drops_a_repeat() -> None:
    watch_gate.configure(read_tier=lambda _uid: "pro", quiet=ALWAYS_QUIET)
    assert watch_gate.admit(_item()) == "hold"
    watch_gate.configure(read_tier=lambda _uid: "pro")
    assert watch_gate.admit(_item()) == "send"
    assert watch_gate.admit(_item()) == "drop", "the same event must not page twice"


def test_items_without_identity_are_never_blocked() -> None:
    watch_gate.configure(quiet=ALWAYS_QUIET)
    assert watch_gate.admit(_item(event_id="")) == "send"
    assert watch_gate.admit(_item(user_id="not-an-int")) == "send"


async def test_watch_alert_is_delivered_outside_quiet_hours() -> None:
    send = AsyncMock()
    assert await alert_worker._deliver_one(_ctx(send), _item()) is True
    assert send.await_count == 1


async def test_quiet_hours_defer_a_watch_alert_without_sending() -> None:
    watch_gate.configure(read_tier=lambda _uid: "pro", quiet=ALWAYS_QUIET)
    send = AsyncMock()
    item = _item()

    assert await alert_worker._deliver_one(_ctx(send), item) is False
    send.assert_not_awaited()
    assert item.pop(alert_worker._GATE_HOLD) is True
    assert not item.get("retry_count"), "a quiet-hours hold is not a failed send"
    assert item["event_id"] not in alert_worker._RECENT_EVENT_IDS, (
        "the worker dedupe must not eat the alert the gate asked to retry"
    )


async def test_other_alert_types_bypass_the_gate() -> None:
    watch_gate.configure(read_tier=lambda _uid: "free", quiet=ALWAYS_QUIET)
    send = AsyncMock()
    item = _item(type="token.whale_move", event_id="evt-whale-1", payload={})

    assert await alert_worker._deliver_one(_ctx(send), item) is True
    assert send.await_count == 1
