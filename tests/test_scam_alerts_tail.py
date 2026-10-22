"""Regression tests for the shill-alert JSONL tailer.

The lane used to die with ``file_read_error err=telling position disabled by
next() call``: ``for line in f`` plus ``f.tell()`` is illegal on a CPython text
file (the iterator buffers ahead and disables tell()), so the exception was
swallowed and the cursor never advanced — the scam-alert publisher read
nothing, forever.
"""

from __future__ import annotations

import json

import pytest

from rugmunchbot.channels.scam_alerts import _publisher as pub


def _alert(ident: str) -> str:
    return json.dumps({"id": ident, "text": f"scam {ident}", "metadata": {"risk": 4}})


@pytest.fixture
def tailer(monkeypatch, tmp_path):
    """Point the tailer at tmp files and return (append, read)."""
    alerts = tmp_path / "shill_alerts.jsonl"
    cursor = tmp_path / "scam_alerts.cursor"
    alerts.write_text("", encoding="utf-8")
    monkeypatch.setattr(pub, "SHILL_ALERTS_FILE", str(alerts))
    monkeypatch.setattr(pub, "CURSOR_FILE", str(cursor))

    def append(text: str) -> None:
        with open(alerts, "a", encoding="utf-8") as handle:
            handle.write(text)

    return append, pub._tail_shill_alerts, cursor, alerts


def test_complete_lines_are_read_and_the_cursor_advances(tailer):
    append, read, cursor, alerts = tailer
    append(_alert("a1") + "\n" + _alert("a2") + "\n")

    alerts_read = read()

    assert [a["id"] for a in alerts_read] == ["a1", "a2"]
    assert int(cursor.read_text()) == alerts.stat().st_size


def test_partial_trailing_write_is_not_consumed(tailer):
    append, read, cursor, alerts = tailer
    append(_alert("a1") + "\n")
    assert [a["id"] for a in read()] == ["a1"]

    # A half-written line (no trailing newline yet) must not be consumed and
    # must not move the cursor past it.
    append('{"id": "a2", "tex')
    assert read() == []
    assert int(cursor.read_text()) == alerts.stat().st_size - len('{"id": "a2", "tex')

    # Completing the same line makes it readable exactly once.
    append('t": "scam"}\n')
    assert [a["id"] for a in read()] == ["a2"]
    assert read() == []


def test_nothing_new_returns_empty_and_keeps_the_cursor(tailer):
    append, read, cursor, _alerts = tailer
    append(_alert("a1") + "\n")
    read()
    position = int(cursor.read_text())

    assert read() == []
    assert int(cursor.read_text()) == position
