"""Smoke + behavior tests for channels/scheduler/_content_rotation."""

from rugmunchbot.channels.scheduler import _content_rotation as cr


def test_slots_shape_and_count():
    assert len(cr.CONTENT_SLOTS) == 8
    for slot in cr.CONTENT_SLOTS:
        assert set(slot) >= {"time_utc", "type", "label", "priority"}
        hh, mm = slot["time_utc"].split(":")
        assert 0 <= int(hh) <= 23
        assert mm == "00"
        assert slot["priority"] in {"high", "medium"}


def test_slots_are_chronological():
    times = [s["time_utc"] for s in cr.CONTENT_SLOTS]
    assert times == sorted(times)


def test_pending_slot_flow_and_reset():
    cr.reset_daily()
    assert cr.get_pending_slot(5) is None  # before the first slot of the day
    first = cr.get_pending_slot(6)
    assert first is not None
    assert first["type"] == "morning_market_report"
    cr.mark_posted(first["type"])
    assert cr.get_pending_slot(6) is None  # 06:00 posted, 08:00 not due yet
    nxt = cr.get_pending_slot(8)
    assert nxt is not None
    assert nxt["type"] == "whale_watch"
    cr.reset_daily()
    assert cr.get_pending_slot(6) is not None
