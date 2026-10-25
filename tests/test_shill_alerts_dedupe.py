"""Tests: stable alert identity, one-alert-per-scam, restart replay.

The proofs required by the task live here:

* 10 channels promoting the same contract produce ONE alert with 10 sources;
* replaying the ledger after a restart re-reads the file and posts nothing;
* the observation key is channel+message+entity+day, the alert key is
  entity+day — both stable across runs.
"""

from __future__ import annotations

from rugmunchbot.channels.shill_alerts.dedupe import (
    AlertLedger,
    AlertRecord,
    alert_key,
    observation_key,
    today,
)

EVM = "0x7aE9b371f65B01277Ffaa2290a189a788A76170d"
DAY = "20261025"


def _record(channel: str, message_id: str, *, post: bool = True) -> AlertRecord:
    return AlertRecord(
        entity=EVM,
        day=DAY,
        channel=channel,
        message_id=message_id,
        source=f"https://t.me/{channel}/{message_id}",
        confidence=0.72,
        route="main",
    )


def test_keys_are_stable_and_scoped_as_documented(tmp_path):
    assert observation_key("darkycalls", "1", EVM, DAY) == observation_key(
        "@DarkyCalls", 1, EVM.lower(), DAY
    )
    assert observation_key("a", "1", EVM, DAY) != observation_key("b", "1", EVM, DAY)
    assert alert_key(EVM, DAY) == alert_key(EVM.lower(), DAY)
    assert alert_key(EVM, DAY) != alert_key(EVM, "20261026")


def test_ten_channels_produce_one_alert_with_ten_sources(tmp_path):
    ledger = AlertLedger(tmp_path / "ledger.jsonl")
    channels = [f"chan{i}" for i in range(10)]

    first = [ledger.record(_record(c, str(i)), post=True) for i, c in enumerate(channels)]

    assert first == [True] + [False] * 9
    sources = ledger.sources(alert_key(EVM, DAY))
    assert len(sources) == 10
    assert sources[0] == "https://t.me/chan0/0"


def test_restart_replay_posts_nothing_new(tmp_path):
    path = tmp_path / "ledger.jsonl"
    AlertLedger(path).record(_record("darkycalls", "1"), post=True)

    revived = AlertLedger(path)  # new process, same durable file
    assert revived.alerted(alert_key(EVM, DAY)) is True
    assert revived.record(_record("darkycalls", "1"), post=True) is False
    assert len(revived.sources(alert_key(EVM, DAY))) == 1


def test_a_failed_send_stays_postable(tmp_path):
    ledger = AlertLedger(tmp_path / "ledger.jsonl")
    ledger.record(_record("darkycalls", "1"), post=False)
    assert ledger.alerted(alert_key(EVM, DAY)) is False
    assert ledger.record(_record("darkycalls", "1"), post=True) is True


def test_next_day_is_a_new_alert(tmp_path):
    ledger = AlertLedger(tmp_path / "ledger.jsonl")
    ledger.record(_record("darkycalls", "1"), post=True)
    tomorrow = AlertRecord(
        entity=EVM,
        day="20261026",
        channel="darkycalls",
        message_id="2",
        source="https://t.me/darkycalls/2",
        confidence=0.7,
        route="main",
    )
    assert ledger.record(tomorrow, post=True) is True


def test_false_positive_suppresses_the_entity(tmp_path):
    ledger = AlertLedger(tmp_path / "ledger.jsonl")
    assert ledger.suppressed(EVM) is False
    ledger.mark_false_positive(EVM)
    assert ledger.suppressed(EVM) is True


def test_torn_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"k": "abc"}\nnot json\n', encoding="utf-8")
    assert AlertLedger(path).rows() == [{"k": "abc"}]


def test_today_is_a_utc_day_bucket():
    assert len(today()) == 8
    assert today().isdigit()
