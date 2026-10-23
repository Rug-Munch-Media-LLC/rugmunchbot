"""Bounded audit persistence: ring buffer, JSONL sink, restart survival."""

from __future__ import annotations

import json

from moderation_fakes import CHAT, LIVE, OTHER, FakeClient, event

from rugmunchbot.community.moderation import ModerationEngine
from rugmunchbot.community.moderation_audit import AuditLog, JsonlAudit


def test_audit_ring_forwards_every_row_to_the_sink(tmp_path) -> None:
    sink = JsonlAudit(tmp_path / "audit.jsonl")
    AuditLog(sink=sink).record({"chat_id": CHAT, "user_id": 1, "level": "warn", "reasons": ["x"]})
    rows = sink.read(CHAT, 5)
    assert len(rows) == 1
    assert rows[0]["level"] == "warn"


def test_engine_rows_survive_a_restart(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    engine = ModerationEngine(clock=lambda: 1000.0, audit_sink=JsonlAudit(path))
    verdict = engine.evaluate(event())
    assert verdict.level == "warn"
    restarted = ModerationEngine(clock=lambda: 1000.0, audit_sink=JsonlAudit(path))
    rows = restarted.actions(CHAT, limit=5)
    assert len(rows) == 1
    assert rows[0]["chat_id"] == CHAT
    assert rows[0]["dry_run"] is True


async def test_enforced_action_is_persisted_without_the_dry_run_flag(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    engine = ModerationEngine(clock=lambda: 1000.0, audit_sink=JsonlAudit(path))
    engine.use_policy(CHAT, LIVE)
    client = FakeClient()
    engine.evaluate(event())
    verdict = engine.evaluate(event())
    assert await engine.enforce(event(), verdict, client) == "ban"
    persisted = JsonlAudit(path).read(CHAT, 5)
    assert persisted[0]["dry_run"] is False
    assert persisted[0]["level"] == "ban"


def test_persisted_audit_stays_bounded_and_keeps_the_newest(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    sink = JsonlAudit(path, max_rows=5)
    for index in range(12):
        sink.append({"chat_id": CHAT, "seq": index})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert json.loads(lines[-1])["seq"] == 11
    assert len(sink.read(CHAT, 10)) == 5


def test_audit_read_ignores_other_chats_and_corrupt_lines(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps({"chat_id": OTHER, "seq": 1})
        + "\nnot json\n"
        + json.dumps({"chat_id": CHAT, "seq": 2})
        + "\n",
        encoding="utf-8",
    )
    assert JsonlAudit(path).read(CHAT, 10) == [{"chat_id": CHAT, "seq": 2}]


def test_missing_audit_file_reads_as_empty(tmp_path) -> None:
    assert JsonlAudit(tmp_path / "absent.jsonl").read(CHAT, 5) == []


def test_unwritable_sink_never_breaks_a_decision() -> None:
    class Exploding:
        def append(self, row: dict) -> None:
            raise RuntimeError("boom")

    engine = ModerationEngine(clock=lambda: 1000.0, audit_sink=Exploding())
    assert engine.evaluate(event()).level == "warn"
    assert len(engine.actions(CHAT, 3)) == 1
