"""Trending disclosure contract — paid badge, footer count, stale, source-down."""

from __future__ import annotations

import time

from rugmunchbot.handlers._market_helpers import _render_trending_text
from rugmunchbot.handlers._market_helpers._disclosure import PAID_BADGE, disclosure_footer
from rugmunchbot.handlers._market_helpers._freshness import freshness_label
from rugmunchbot.handlers._market_helpers._disclosure import TRENDING_UNAVAILABLE_SOURCE


def _tok(i: int, *, paid: bool = False, fetched: float | None = None) -> dict:
    return {
        "address": f"0x{i:040x}",
        "symbol": f"T{i}",
        "name": f"Token{i}",
        "chain": "solana",
        "price_usd": 0.01 * i,
        "volume_24h": 1_000_000 / i,
        "liquidity_usd": 50_000 * i,
        "change_24h": 5.0 * i,
        "paid_placement": paid,
        "fetched_at": fetched,
    }


def test_mixed_top5_marks_every_paid_row_and_counts_footer() -> None:
    now = time.time()
    toks = [_tok(i, paid=(i in (2, 4)), fetched=now) for i in range(1, 6)]
    text = _render_trending_text(toks, "solana", "24h")
    assert text.count(PAID_BADGE) == 2
    assert "2 of 5 are paid placements" in text
    assert "never ranked as organic" in text


def test_organic_only_states_zero_paid() -> None:
    toks = [_tok(i, fetched=time.time()) for i in range(1, 6)]
    text = _render_trending_text(toks, "solana", "24h")
    assert PAID_BADGE not in text
    assert "0 of 5 are paid placements" in text


def test_footer_math_is_clamped() -> None:
    assert disclosure_footer(5, 0).startswith("0 of 5")
    assert disclosure_footer(5, 9).startswith("5 of 5")
    assert disclosure_footer(0, 0).startswith("0 of 0")


def test_stale_feed_is_labelled_after_ten_minutes() -> None:
    toks = [_tok(i, fetched=time.time() - 900) for i in range(1, 6)]
    assert "stale" in _render_trending_text(toks, "solana", "24h")


def test_fresh_feed_is_not_labelled() -> None:
    assert freshness_label(time.time() - 60) == ""
    assert freshness_label(None) == ""


def test_source_down_is_typed_not_empty() -> None:
    assert TRENDING_UNAVAILABLE_SOURCE == "unavailable (source)"
