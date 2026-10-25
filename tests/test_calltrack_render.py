"""Call-tracking cards: no invented numbers, escaped input, honest leaderboard."""

from __future__ import annotations

from rugmunchbot.calltrack.model import parse_call
from rugmunchbot.calltrack.render import render_call_card, render_calls, render_leaderboard

ROW = {
    "chat_id": -1,
    "user_id": 7,
    "username": "alice",
    "token_address": "So11111111111111111111111111111111111111112",
    "chain": "solana",
    "note": "",
    "called_at": 1000,
}


def test_parse_call_recognises_evm_and_solana():
    evm = parse_call(["0x" + "a" * 40, "aping", "hard"])
    assert evm == {"address": "0x" + "a" * 40, "chain": "ethereum", "note": "aping hard"}
    sol = parse_call(["So11111111111111111111111111111111111111112"])
    assert sol["chain"] == "solana"
    assert sol["note"] == ""


def test_parse_call_accepts_ticker_without_guessing_chain():
    parsed = parse_call(["$wif"])
    assert parsed == {"address": "$wif", "chain": "", "note": ""}


def test_parse_call_rejects_garbage_and_empty():
    assert parse_call([]) is None
    assert parse_call(["<script>"]) is None


def test_card_states_first_caller_and_never_quotes_a_price():
    card = render_call_card(ROW, stored=True)
    assert "First caller: @alice" in card
    assert "$" not in card.replace("/lb", "").replace("/calls", "").replace("/fc", "")
    assert "Call logged" in card


def test_card_reports_existing_call_without_overwriting():
    card = render_call_card(ROW, stored=False)
    assert "Already called" in card
    assert "First caller wins" in card


def test_card_escapes_username_html():
    card = render_call_card({**ROW, "username": "<b>x</b>"}, stored=True)
    assert "<b>x</b>" not in card
    assert "&lt;b&gt;" in card


def test_calls_list_truncates_addresses():
    rows = [{**ROW, "token_address": "0x" + "a" * 40}]
    text = render_calls(rows)
    assert "0xaaaa…aaaa" in text


def test_empty_states_point_at_the_command():
    assert "/call" in render_calls([])
    assert "No calls logged" in render_leaderboard([], "7d")


def test_leaderboard_disclaims_returns():
    text = render_leaderboard(
        [{"user_id": 1, "username": "a", "calls": 3, "last_call": 1}], "all time"
    )
    assert "🥇 @a — 3 call(s)" in text
    assert "Returns are not claimed" in text
