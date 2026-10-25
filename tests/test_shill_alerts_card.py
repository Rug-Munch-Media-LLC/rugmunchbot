"""Tests: grouping, evidence card completeness, quiet hours, delivery gating."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rugmunchbot.channels.shill_alerts.card import render_alert, render_review_line
from rugmunchbot.channels.shill_alerts.dedupe import AlertLedger
from rugmunchbot.channels.shill_alerts.deliver import deliver_alerts, in_quiet_hours
from rugmunchbot.channels.shill_alerts.gate import MAIN, REVIEW
from rugmunchbot.channels.shill_alerts.plan import plan_alerts

EVM = "0x7aE9b371f65B01277Ffaa2290a189a788A76170d"
DAY = "20261025"


def _rows(count: int = 1, *, text: str = "") -> list[dict]:
    return [
        {
            "channel": f"chan{i}",
            "message_id": str(100 + i),
            "text": f"CA:{EVM} {text}",
            "link": f"https://t.me/chan{i}/{100 + i}",
        }
        for i in range(count)
    ]


def test_ten_channels_collapse_into_one_alert_with_ten_sources():
    alerts = plan_alerts(_rows(10, text="guaranteed 100x join my group"), day=DAY)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.verdict.route == MAIN
    assert len(alert.sources) == 10
    assert len(alert.channels) == 10
    assert "cross_channel_repeat" in {s.name for s in alert.signals}


def test_single_signal_item_stays_in_review():
    alerts = plan_alerts([_rows(1)[0]], day=DAY)
    assert len(alerts) == 1
    assert alerts[0].verdict.route == REVIEW
    assert "review ·" in render_review_line(alerts[0])


def test_messages_without_an_entity_are_ignored():
    assert plan_alerts([{"channel": "c", "message_id": "1", "text": "gm"}], day=DAY) == []


def test_card_is_an_allegation_with_complete_evidence():
    alert = plan_alerts(_rows(10, text="guaranteed 100x join my group"), day=DAY)[0]
    text, buttons = render_alert(alert)
    assert "Alleged" in text
    assert "Allegation, not a verdict" in text
    assert EVM[:8] in text
    assert "ethereum" in text
    assert "Confidence:" in text
    assert "Sources (10):" in text
    assert "https://t.me/chan0/100" in text
    assert len(buttons) == 2
    assert len(buttons[0]) == 2
    for row in buttons:
        for label, data in row:
            assert label
            assert data.startswith("shill:")
            assert len(data) <= 64


def test_card_coerces_risk_without_fabricating_it():
    alert = plan_alerts(_rows(3, text="guaranteed"), day=DAY)[0]
    assert (alert.risk or {}) == {}
    text, _ = render_alert(alert)
    assert "Scanner" not in text or "flagged" not in text


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(23, True), (3, True), (6, False), (12, False), (22, True)],
)
def test_quiet_hours_wrap_midnight(hour, expected):
    now = datetime(2026, 10, 25, hour, 30, tzinfo=UTC)
    assert in_quiet_hours(now, "22-06") is expected


def test_malformed_quiet_window_never_silences_the_lane():
    now = datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
    assert in_quiet_hours(now, "") is False
    assert in_quiet_hours(now, "nope") is False


class _Bot:
    """Minimal bot: records sends, optionally fails the first one."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append(kwargs)
        return type("M", (), {"message_id": len(self.sent)})()


def _fake_send(fail: bool = False):
    """Stand-in for ``_safe_send`` (the DI seam on ``deliver_alerts``)."""

    async def send(bot, chat_id, text, *, content_type=None):
        if fail:
            return False
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML") is not None

    return send


@pytest.mark.asyncio
async def test_delivery_is_held_and_not_marked_during_quiet_hours(tmp_path):
    ledger = AlertLedger(tmp_path / "l.jsonl")
    alerts = plan_alerts(_rows(4, text="guaranteed 100x join my group"), day=DAY)
    report = await deliver_alerts(
        _Bot(),
        alerts,
        chat_id="-100",
        ledger=ledger,
        now=datetime(2026, 10, 25, 23, 0, tzinfo=UTC),
        send=_fake_send(),
    )
    assert report["reason"] == "quiet_hours"
    assert report["posted"] == 0
    assert ledger.rows() == []


@pytest.mark.asyncio
async def test_failed_send_is_retried_on_the_next_tick(tmp_path):
    ledger = AlertLedger(tmp_path / "l.jsonl")
    alerts = plan_alerts(_rows(4, text="guaranteed 100x join my group"), day=DAY)
    now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    failed = await deliver_alerts(
        _Bot(), alerts, chat_id="-1002", ledger=ledger, now=now, send=_fake_send(fail=True)
    )
    assert failed["failed"] == 1
    assert ledger.rows() == []

    bot = _Bot()
    ok = await deliver_alerts(
        bot, alerts, chat_id="-1003", ledger=ledger, now=now, send=_fake_send()
    )
    assert ok["posted"] == 1
    assert len(bot.sent) == 2  # card + operator action row


@pytest.mark.asyncio
async def test_second_run_of_the_same_entity_posts_nothing(tmp_path):
    ledger = AlertLedger(tmp_path / "l.jsonl")
    alerts = plan_alerts(_rows(4, text="guaranteed 100x join my group"), day=DAY)
    now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    first = await deliver_alerts(
        _Bot(), alerts, chat_id="-1004", ledger=ledger, now=now, send=_fake_send()
    )
    second = await deliver_alerts(
        _Bot(), alerts, chat_id="-1005", ledger=ledger, now=now, send=_fake_send()
    )
    assert first["posted"] == 1
    assert second["posted"] == 0
    assert second["duplicate"] == 1


@pytest.mark.asyncio
async def test_suppressed_entity_is_never_posted(tmp_path):
    ledger = AlertLedger(tmp_path / "l.jsonl")
    alerts = plan_alerts(_rows(4, text="guaranteed 100x join my group"), day=DAY)
    ledger.mark_false_positive(EVM, day=DAY)
    report = await deliver_alerts(
        _Bot(),
        alerts,
        chat_id="-1006",
        ledger=ledger,
        now=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
        send=_fake_send(),
    )
    assert report["posted"] == 0
