"""Test wave 25 — market helper pure surface (time, badges, windows, names).

Contracts read from source (2026-08-22). Pure functions only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugmunchbot.handlers._market_helpers import (
    _age_badge,
    _chain_badge,
    _clean_token_name,
    _freshness_bonus,
    _liq_badge,
    _mover_bonus,
    _normalize_address,
    _resolve_chain,
    _risk_emoji,
    _sort_trending,
    _time_ago,
    _token_age_hours,
    _trending_display,
    _window_change,
    _window_note,
)

# ── relative time ────────────────────────────────────────────────────────────


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def test_time_ago_buckets() -> None:
    assert _time_ago(None) == ""
    assert _time_ago("") == ""
    assert _time_ago(_iso(timedelta(seconds=30))) == "just now"  # future stamp
    assert _time_ago(_iso(timedelta(seconds=-30))).endswith("s ago")
    assert _time_ago(_iso(timedelta(minutes=-5))) == "5m ago"
    assert _time_ago(_iso(timedelta(hours=-3))).endswith("h ago")
    assert _time_ago(_iso(timedelta(days=-2))) == "2d ago"


def test_time_ago_malformed_iso_degrades_to_empty() -> None:
    # Fixed 2026-08-22: malformed ISO from an upstream source degrades to
    # an empty label instead of crashing the trending renderer.
    assert _time_ago("not-a-timestamp") == ""


# ── chain / badge helpers ────────────────────────────────────────────────────


def test_resolve_chain_aliases_and_default() -> None:
    assert _resolve_chain(None) == "solana"
    assert _resolve_chain("") == "solana"
    assert _resolve_chain(" SOL ") == "solana"
    assert _resolve_chain("eth") == "ethereum"
    assert _resolve_chain("base") == "base"  # canonical passthrough
    assert _resolve_chain("sui") == "sui"  # unknown lowercased verbatim


def test_chain_badge_known_and_unknown() -> None:
    assert _chain_badge("solana") == "\U0001f7e3"
    assert _chain_badge("ETHEREUM") == "\U0001f535"
    assert _chain_badge("madeup") == "\u26aa"


def test_age_badge_ladder_and_garbage() -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    assert _age_badge(None) == "\u2753"
    assert _age_badge(now_ms - 3600_000) == "\U0001f195"  # 1h -> fresh
    assert _age_badge(now_ms - 3 * 86_400_000) == "\U0001f331"  # 3d
    assert _age_badge(now_ms - 10 * 86_400_000) == "\U0001f33f"  # 10d
    assert _age_badge(now_ms - 60 * 86_400_000) == "\U0001f333"  # 60d
    assert _age_badge("garbage") == "\u2753"


def test_liq_badge_tiers() -> None:
    assert _liq_badge(2_000_000) == "\U0001f4a7 Deep"
    assert _liq_badge(500_000) == "\U0001f4a6 Medium"
    assert _liq_badge(50_000) == "\U0001f4a7 Shallow"
    assert _liq_badge(100) == "\u26a0\ufe0f Critical"


def test_risk_emoji_bands_and_junk() -> None:
    assert _risk_emoji(None) == "\u26aa"
    assert _risk_emoji(85) == "\U0001f534"
    assert _risk_emoji(50) == "\U0001f7e1"
    assert _risk_emoji(10) == "\U0001f7e2"
    assert _risk_emoji("high") == "\u26aa"


def test_normalize_address() -> None:
    assert _normalize_address("  0xAbCd  ") == "0xabcd"


# ── age / freshness / mover bonuses ──────────────────────────────────────────


def test_token_age_hours_sources_and_guards() -> None:
    now_ms = datetime.now(UTC).timestamp() * 1000
    assert _token_age_hours({}) is None
    assert _token_age_hours({"created_at": "junk"}) is None
    assert _token_age_hours({"created_at": 0}) is None
    one_hour = {"created_at": now_ms - 3_600_000}
    assert 0.9 <= (_token_age_hours(one_hour) or 0) <= 1.1
    legacy = {"pairCreatedAt": now_ms - 7_200_000}
    assert 1.9 <= (_token_age_hours(legacy) or 0) <= 2.1
    future = {"created_at": now_ms + 86_400_000}
    assert _token_age_hours(future) == 0.0  # clamped, never negative


def test_freshness_bonus_windows() -> None:
    now_ms = datetime.now(UTC).timestamp() * 1000
    assert _freshness_bonus({}) == 0  # unknown age -> no invented freshness
    assert _freshness_bonus({"created_at": now_ms - 3600_000}) == 2  # < 24h
    assert _freshness_bonus({"created_at": now_ms - 72 * 3600_000}) == 1  # < 7d
    assert _freshness_bonus({"created_at": now_ms - 400 * 3600_000}) == 0


def test_mover_bonus_abs_both_directions() -> None:
    assert _mover_bonus({"change_24h": 150}) == 2
    assert _mover_bonus({"change_24h": -120}) == 2  # red action counts too
    assert _mover_bonus({"change_24h": 30}) == 1
    assert _mover_bonus({"change_24h": 5}) == 0
    assert _mover_bonus({}) == 0


# ── window logic ─────────────────────────────────────────────────────────────


def test_window_change_field_fallbacks() -> None:
    assert _window_change({"change_24h": 5}, "24h") == 5.0
    assert _window_change({"change_1h": 2, "change_24h": 9}, "1h") == 2.0
    assert _window_change({}, "1h") == 0.0  # falls back to absent 24h -> 0
    assert _window_change({"change_24h": "junk"}, "24h") == 0.0


def test_sort_trending_windows() -> None:
    tokens = [
        {"change_24h": 5, "volume_24h": 10},
        {"change_24h": -90, "volume_24h": 20},
        {"change_24h": 40, "volume_24h": 30},
    ]
    day = _sort_trending(tokens, "24h")
    assert [abs(t["change_24h"]) for t in day] == [90, 40, 5]  # |move| desc
    hour = _sort_trending(tokens, "1h")
    assert [t["volume_24h"] for t in hour] == [30, 20, 10]  # volume-ranked
    week = _sort_trending([{"change_7d": 300, "change_24h": 1}, {"change_7d": -500}], "7d")
    assert abs(week[0]["change_7d"]) == 500
    # 7d without series data degrades to 24h magnitudes.
    no_series = _sort_trending([{"change_24h": 2}, {"change_24h": 80}], "7d")
    assert no_series[0]["change_24h"] == 80


def test_window_note_honesty_rules() -> None:
    assert "ranked by volume" in _window_note("1h", [])
    assert "using 24h deltas" in _window_note("7d", [{}])
    assert _window_note("7d", [{"change_7d": 1}]) == ""
    assert _window_note("24h", []) == ""


# ── token name hygiene ───────────────────────────────────────────────────────


def test_clean_token_name_garbage_patterns() -> None:
    assert _clean_token_name("Pepe on robinhood now", "PEPE") == ("Pepe", "PEPE")
    assert _clean_token_name("", "DOGE") == ("DOGE", "DOGE")
    assert _clean_token_name("Unknown", "") == ("Unknown", "???")
    long_name = "T" * 41
    assert _clean_token_name(long_name, "SYM") == ("SYM", "SYM")
    sentence = "one two three four five six seven"
    assert _clean_token_name(sentence, "SYM") == ("SYM", "SYM")


def test_clean_token_name_symbol_in_name_prefers_name() -> None:
    assert _clean_token_name("Pepe Coin", "PEPE") == ("Pepe Coin", "PEPE")


def test_trending_display_address_shaped_names_rejected() -> None:
    addr_like = {"name": "0xabc123", "symbol": "tok"}
    assert _trending_display(addr_like) == ("tok", "tok")
    normal = {"name": "Pepe", "symbol": "PEPE"}
    assert _trending_display(normal) == ("Pepe", "PEPE")
