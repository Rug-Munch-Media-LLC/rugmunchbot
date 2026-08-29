"""KOL Hall of Shame — report-card model, store round-trip, formatters.

Covers the 2026-08-29 KOL tracking deliverable:

* call classification + token extraction (pure)
* report-card aggregation: deletions >> shilling >> scam-token calls
* shame-score + letter-grade bands + hall-of-shame ranking
* HTML formatters (Telegram parse_mode=html)
* SQLite store round-trip (record → list → delete → export JSONL)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rugmunchbot.channels.kol_hall_of_shame import (
    KolCall,
    classify_call,
    compute_report_card,
    extract_tokens,
    format_hall_of_shame_post,
    format_report_card,
    grade_for,
    rank_hall_of_shame,
)


def _call(
    handle: str = "shillking",
    tweet_id: str = "t1",
    text: str = "$MOON gem 100x aping in",
    token_symbol_val: str = "MOON",  # noqa: S107
    addr: str = "",
    deleted: bool = False,
) -> KolCall:
    return KolCall(
        handle=handle,
        tweet_id=tweet_id,
        token_symbol=token_symbol_val,
        token_address=addr,
        call_type=classify_call(text),
        tweet_text=text,
        posted_at=datetime.now(UTC).isoformat(),
        source_url=f"https://x.com/{handle}/status/{tweet_id}",
        is_deleted=deleted,
        deleted_at=datetime.now(UTC).isoformat() if deleted else "",
        captured_at=datetime.now(UTC).isoformat(),
    )


def _buy_call(handle: str = "decent", tweet_id: str = "t9", token: str = "PEPE") -> KolCall:  # noqa: S107
    """A plain (non-hype) call — the 'clean' control."""
    return _call(
        handle=handle,
        tweet_id=tweet_id,
        text=f"Bought ${token}, position open",
        token_symbol_val=token,
    )


# ══════════════════════════════════════════════════════════════════════════
# Classification + extraction (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_classify_call_types():
    assert classify_call("$MOON is a gem, 100x incoming") == "shill"
    assert classify_call("Bought $PEPE, aping in") == "buy"  # plain call, no hype
    assert classify_call("This token will rug, exit now") == "rug"
    assert classify_call("The weather is nice today") == "other"
    assert classify_call("") == "other"


def test_extract_tokens():
    ticker, addr = extract_tokens("$MOON pumping hard 0xabc123def456")
    assert ticker == "MOON"
    assert addr == "0xabc123def456"
    ticker2, addr2 = extract_tokens("no tokens here")
    assert ticker2 == ""
    assert addr2 == ""


# ══════════════════════════════════════════════════════════════════════════
# Report card (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_compute_report_card_counts():
    calls = [
        _call(tweet_id="1"),
        _call(tweet_id="2", deleted=True),
        _call(tweet_id="3", deleted=True),
        _call(tweet_id="4", text="This will rug hard"),
    ]
    card = compute_report_card(calls)
    assert card.handle == "shillking"
    assert card.total_calls == 4
    assert card.deleted_calls == 2
    assert card.shill_calls == 4  # 3 shill + 1 rug (all counted as shill-ish)
    assert card.tokens_mentioned == 1  # all $MOON


def test_compute_report_card_empty():
    card = compute_report_card([])
    assert card.handle == ""
    assert card.total_calls == 0
    assert card.grade == "N/A"


def test_shame_score_weights_deletion_heavily():
    clean = compute_report_card([_buy_call(tweet_id="1"), _buy_call(tweet_id="2")])
    deleted = compute_report_card(
        [_call(tweet_id="1", deleted=True), _call(tweet_id="2", deleted=True)]
    )
    assert clean.score == 0.0  # plain calls, no deletions → clean card
    assert deleted.score > clean.score * 10  # deletion dominates


def test_grade_bands():
    assert grade_for(0.0) == "A+"
    assert grade_for(4.9) == "A+"
    assert grade_for(5.0) == "A"
    assert grade_for(15.0) == "B"
    assert grade_for(30.0) == "C"
    assert grade_for(50.0) == "D"
    assert grade_for(80.0) == "F"
    assert grade_for(200.0) == "F"


def test_hall_of_shame_ranking_worst_first():
    bad = compute_report_card(
        [
            _call(tweet_id="1", handle="scammer", deleted=True),
            _call(tweet_id="2", handle="scammer", deleted=True),
        ]
    )
    ok = compute_report_card([_buy_call(tweet_id="3", handle="decent")])
    hall = rank_hall_of_shame([ok, bad])
    assert len(hall) == 1  # decent never enters the hall
    assert hall[0].handle == "scammer"
    assert bad.in_hall_of_shame
    assert not ok.in_hall_of_shame


# ══════════════════════════════════════════════════════════════════════════
# Formatters
# ══════════════════════════════════════════════════════════════════════════


def test_format_report_card_html():
    card = compute_report_card([_call(tweet_id="1", deleted=True)])
    text = format_report_card(card)
    assert "KOL report card" in text
    assert "@shillking" in text
    assert "deleted calls: 1" in text


def test_format_hall_of_shame_post():
    card = compute_report_card(
        [
            _call(tweet_id="1", deleted=True),
            _call(tweet_id="2", deleted=True),
            _call(tweet_id="3", text="$MOON 100x now"),
        ]
    )
    post = format_hall_of_shame_post([card])
    assert "KOL HALL OF SHAME" in post
    assert "@shillking" in post
    assert "deleted" in post
    assert format_hall_of_shame_post([]) == ""


# ══════════════════════════════════════════════════════════════════════════
# Store round-trip (tmp SQLite)
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    from rugmunchbot import db as db_pkg

    monkeypatch.setattr(db_pkg, "DB_PATH", str(tmp_path / "test_bot.db"))
    from rugmunchbot.db._base import init_db

    init_db()
    return tmp_path


def test_store_round_trip(tmp_db):
    from rugmunchbot.db import (
        get_kol_call,
        get_kol_scores,
        list_kol_calls,
        mark_kol_call_deleted,
        record_kol_call,
        upsert_kol_score,
    )

    assert record_kol_call(
        handle="shillking", tweet_id="t1", token_symbol="MOON", tweet_text="$MOON 100x"
    )
    # Upsert by tweet_id — re-recording the same tweet doesn't dup.
    assert record_kol_call(
        handle="shillking", tweet_id="t1", token_symbol="MOON", tweet_text="$MOON 100x"
    )
    rows = list_kol_calls(handle="shillking")
    assert len(rows) == 1

    assert mark_kol_call_deleted("t1")
    row = get_kol_call("t1")
    assert row["is_deleted"] == 1
    assert row["deleted_at"]

    upsert_kol_score(
        handle="shillking",
        total_calls=1,
        deleted_calls=1,
        shill_calls=1,
        scam_token_calls=0,
        tokens_mentioned=1,
        score=57.0,
        grade="F",
    )
    scores = get_kol_scores()
    assert len(scores) == 1
    assert scores[0]["grade"] == "F"


def test_store_export_jsonl_callstore_shape(tmp_db):
    from rugmunchbot.db import export_kol_calls_jsonl, record_kol_call

    record_kol_call(
        handle="shillking",
        tweet_id="t1",
        token_symbol="MOON",
        tweet_text="$MOON 100x",
        source_url="https://x.com/shillking/status/t1",
    )
    path = str(tmp_db / "scammer_calls.jsonl")
    written = export_kol_calls_jsonl(path)
    assert written == 1
    import json

    with open(path) as fh:
        record = json.loads(fh.readline())
    # The rmi-backend site router (GET /api/v1/kol/scammer-calls) reads this
    # exact schema from app/databus/scammer_calls.json.
    assert record["handle"] == "shillking"
    assert record["channel"] == "x"
    assert record["token_symbol"] == "MOON"
    assert record["source_url"].startswith("https://x.com/")
