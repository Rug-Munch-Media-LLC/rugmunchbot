"""Content-automation contracts (no network, no Telegram).

Locks the four promises of the unified lane:
1. every post carries evidence + source + timestamp + risk note;
2. dedupe is by stable event key, then a per-channel hourly rate limit;
3. runs are dry-run by default and never touch Telegram;
4. no invented numbers — a missing delta/wallet/amount means no claim.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from rugmunchbot.channels.automation import (
    LaneGate,
    render_call,
    run_lane,
    select_calls,
    select_liquidations,
    select_moves,
    select_odds,
)
from rugmunchbot.smart_money import Feed, render_hl, render_move, render_prediction

FIXTURES = Path(__file__).parent / "fixtures" / "content_automation"


def _fixture(name: str) -> dict[str, Any]:
    body = json.loads((FIXTURES / f"{name}.json").read_text())
    assert body["_label"].startswith("SYNTHETIC FIXTURE"), "fixtures must be labelled"
    return body


def _feed(name: str, kind: str) -> Feed:
    return Feed(
        kind=kind,
        items=tuple(_fixture(name)["items"]),
        source="fixture",
        updated_at="2026-10-24T00:00:00Z",
        tried=("fixture",),
    )


def _blocks(post: Any) -> str:
    return str(post)


# ── 1. the five-block post contract ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("post", "bullet"),
    [
        (render_move(_feed("moves", "moves"), _fixture("moves")["items"][0]), "Size:"),
        (render_hl(_feed("hl", "hl"), _fixture("hl")["items"][0]), "Open interest"),
        (
            render_prediction(
                _feed("predictions", "predictions"), _fixture("predictions")["items"][0]
            ),
            "YES",
        ),
        (render_call(_feed("launches", "calls"), _fixture("launches")["items"][0]), "Liquidity:"),
    ],
)
def test_post_carries_evidence_source_time_risk(post: Any, bullet: str) -> None:
    text = _blocks(post)
    assert bullet in text
    assert "<b>Source:</b>" in text
    assert "<b>Time:</b> 2026-10-24 00:00Z" in text
    assert "<b>Risk:</b>" in text


# ── 2. dedupe + rate limit ──────────────────────────────────────────────────


def test_duplicate_event_is_suppressed() -> None:
    gate = LaneGate(ttl=600, max_per_hour=5, clock=lambda: 1000.0)
    assert gate.allow("move:FIXTUREtx-move-0001") == "ok"
    assert gate.allow("move:FIXTUREtx-move-0001") == "dupe"


def test_hourly_rate_limit_caps_the_lane() -> None:
    gate = LaneGate(ttl=600, max_per_hour=2, clock=lambda: 1000.0)
    assert gate.allow("a") == "ok"
    assert gate.allow("b") == "ok"
    assert gate.allow("c") == "rate"


def test_move_selection_drops_walletless_rows() -> None:
    plans = select_moves(_feed("moves", "moves"), limit=2)
    assert [p.event_key for p in plans] == ["move:FIXTUREtx-move-0001", "move:FIXTUREtx-move-0002"]
    assert "9.00M" not in _blocks(plans[0].post)


# ── 3. no invented numbers ─────────────────────────────────────────────────


def test_odds_shift_requires_a_supplied_delta() -> None:
    keys = [p.event_key for p in select_odds(_feed("predictions", "predictions"), limit=5)]
    assert keys == ["odds:FIXTURE-mkt-shift", "odds:FIXTURE-mkt-kalshi"]


def test_kalshi_is_aggregate_only() -> None:
    plans = select_odds(_feed("predictions", "predictions"), limit=5)
    kalshi = next(p for p in plans if "kalshi" in p.event_key)
    assert "FIXTUREwalletSHOULD-BE-STRIPPED" not in _blocks(kalshi.post)
    assert "Wallet" not in _blocks(kalshi.post)
    assert "no wallet identity" in kalshi.risk_note


def test_liquidation_lane_prefers_pressure_over_movers() -> None:
    plans = select_liquidations(_feed("hl", "hl"), limit=1)
    assert len(plans) == 1
    assert "LIQUIDATION MAP" in _blocks(plans[0].post)
    assert "Wallet" not in _blocks(plans[0].post)


def test_call_event_key_is_the_contract_address() -> None:
    plans = select_calls(_feed("launches", "calls"), limit=2)
    assert plans[0].event_key == "call:FIXTUREmintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1"


def test_absent_field_is_absent_not_zero() -> None:
    post = render_call(_feed("launches", "calls"), _fixture("launches")["items"][1])
    assert "Holders" not in _blocks(post)
    assert "Liquidity: <b>$12.0K</b>" in _blocks(post)


# ── 4. dry run default + honest empty ──────────────────────────────────────


class _Bot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)


@pytest.mark.asyncio
async def test_dry_run_is_default_and_never_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RMI_SMARTMONEY_POST", raising=False)
    bot = _Bot()
    result = await run_lane("move", bot=bot, feed=_feed("moves", "moves"))
    assert result["status"] == "dry_run"
    assert result["posted"] == 0
    assert bot.sent == []


@pytest.mark.asyncio
async def test_empty_feed_reports_honest_empty() -> None:
    empty = Feed(kind="moves", note="no moves", tried=("fixture",))
    result = await run_lane("move", feed=empty)
    assert result["status"] == "empty"
    assert result["note"] == "no moves"
    assert result["posted"] == 0
    assert result["candidates"] == 0


@pytest.mark.asyncio
async def test_second_run_of_same_event_is_suppressed() -> None:
    gate = LaneGate(ttl=600, max_per_hour=5, clock=lambda: 1000.0)
    first = await run_lane("move", feed=_feed("moves", "moves"), gate=gate)
    second = await run_lane("move", feed=_feed("moves", "moves"), gate=gate)
    assert first["candidates"] == 1
    assert second["status"] == "suppressed"
    assert second["suppressed"][0]["key"] == "move:FIXTUREtx-move-0001"
