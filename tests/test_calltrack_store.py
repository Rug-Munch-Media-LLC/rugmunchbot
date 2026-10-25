"""Call tracking: first caller wins, counts are honest, windows filter."""

from __future__ import annotations

import sqlite3

from rugmunchbot.calltrack.store import (
    first_call,
    group_calls,
    init_calls_schema,
    leaderboard,
    record_call,
)

CHAT = -1001234567890


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    init_calls_schema(conn)
    return conn


def test_first_caller_wins_and_second_is_rejected():
    conn = _db()
    stored, row = record_call(
        CHAT,
        1,
        "alice",
        "So11111111111111111111111111111111111111112",
        chain="solana",
        conn=conn,
        now=1000,
    )
    assert stored is True
    assert row["user_id"] == 1
    stored2, row2 = record_call(
        CHAT,
        2,
        "bob",
        "So11111111111111111111111111111111111111112",
        chain="solana",
        conn=conn,
        now=1100,
    )
    assert stored2 is False
    assert row2["user_id"] == 1


def test_first_call_is_per_chat_not_global():
    conn = _db()
    record_call(CHAT, 1, "alice", "0x" + "a" * 40, now=1000, conn=conn)
    stored, row = record_call(-100999, 9, "carol", "0x" + "a" * 40, now=1001, conn=conn)
    assert stored is True
    assert row["user_id"] == 9


def test_first_call_is_case_insensitive_and_case_cannot_duplicate():
    conn = _db()
    record_call(CHAT, 1, "alice", "0xAbCdEf" + "0" * 34, now=1000, conn=conn)
    assert first_call(CHAT, "0xabcdef" + "0" * 34, conn=conn)["user_id"] == 1
    stored, row = record_call(CHAT, 2, "bob", "0XABCDEF" + "0" * 34, now=1001, conn=conn)
    assert stored is False
    assert row["user_id"] == 1


def test_group_calls_newest_first_and_limited():
    conn = _db()
    for i in range(5):
        record_call(CHAT, i, f"u{i}", f"tok{i}", now=1000 + i, conn=conn)
    rows = group_calls(CHAT, limit=3, conn=conn)
    assert [r["token_address"] for r in rows] == ["tok4", "tok3", "tok2"]


def test_leaderboard_counts_and_since_filter():
    conn = _db()
    record_call(CHAT, 1, "alice", "a1", now=100, conn=conn)
    record_call(CHAT, 1, "alice", "a2", now=200, conn=conn)
    record_call(CHAT, 2, "bob", "b1", now=300, conn=conn)
    board = leaderboard(CHAT, conn=conn)
    assert [(r["user_id"], r["calls"]) for r in board] == [(1, 2), (2, 1)]
    recent = leaderboard(CHAT, since_ts=250, conn=conn)
    assert [(r["user_id"], r["calls"]) for r in recent] == [(2, 1)]


def test_leaderboard_is_chat_scoped():
    conn = _db()
    record_call(CHAT, 1, "alice", "a1", now=100, conn=conn)
    assert leaderboard(-100999, conn=conn) == []
