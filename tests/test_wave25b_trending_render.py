"""Test wave 25b — trending merge, HTML renderer, keyboard builder.

Contracts read from source (2026-08-22). Pure functions only.
"""

from __future__ import annotations

from rugmunchbot.handlers._market_helpers import (
    _build_trending_kb,
    _merge_trending_sources,
    _render_trending_text,
)

# ── source merge / dedupe ────────────────────────────────────────────────────


def test_merge_dedupes_by_address_and_prefers_quality() -> None:
    rug = [
        {"address": "0xA", "name": "TokenA", "symbol": "TA", "volume_24h": 5, "liquidity_usd": 5}
    ]
    dex = [{"address": "0xa", "name": "Unknown", "symbol": ""}]  # same token, worse data
    fresh = [{"address": "0xB", "name": "FreshB", "symbol": "FB"}]
    out = _merge_trending_sources(rug, dex, fresh, limit=10)
    addrs = [t["address"] for t in out]
    assert "0xA" in addrs
    assert "0xB" in addrs
    a_row = next(t for t in out if t["address"] == "0xA")
    assert a_row["name"] == "TokenA"  # richer rugcharts row won the dedupe
    assert all(t.get("name") != "Unknown" for t in out)  # placeholder names filtered


def test_merge_drops_addressless_and_applies_limit() -> None:
    rug = [
        {"name": "NoAddr"},
        {"address": "0x1", "name": "A", "symbol": "A"},
        {"address": "0x2", "name": "B", "symbol": "B"},
        {"address": "0x3", "name": "C", "symbol": "C"},
    ]
    out = _merge_trending_sources(rug, None, None, limit=2)
    assert len(out) == 2
    assert all(t.get("address") for t in out)


# ── HTML renderer ────────────────────────────────────────────────────────────


def test_render_trending_text_rows_badges_and_note() -> None:
    tokens = [
        {
            "address": "0xAAAABBBBCCCCDDDD",
            "name": "Pepe",
            "symbol": "PEPE",
            "change_24h": 12.5,
            "volume_24h": 1000,
            "liquidity_usd": 50_000,
            "risk_score": 80,
            "bundle_detected": True,
        },
        {
            "address": "0xFFFF0000111122223333",
            "name": "",
            "symbol": "",
            "change_24h": -3,
            "volume_24h": 10,
        },
    ]
    text = _render_trending_text(tokens, "solana", "1h")
    assert "🔥 <b>Trending — Solana · 1h</b>" in text
    assert "<b>Pepe</b> (PEPE)" in text
    assert "🚨" in text  # bundle warning badge
    # Unknown-name token renders the truncated-address display form.
    assert "0xFFFF00...1122" in text.replace("…", "...") or "0xFFFF00" in text
    assert "ranked by volume" in text  # honest 1h-window footnote
    assert "Chains:" in text


def test_render_trending_text_paragraph_break_between_cards() -> None:
    tokens = [
        {
            "address": "0xAAAABBBBCCCCDDDD",
            "name": "Pepe",
            "symbol": "PEPE",
            "change_24h": 12.5,
            "volume_24h": 1000,
        },
        {
            "address": "0xFFFF0000111122223333",
            "name": "Doge",
            "symbol": "DOGE",
            "change_24h": -3,
            "volume_24h": 10,
        },
    ]
    text = _render_trending_text(tokens, "solana", "24h")
    # Card separation: blank line before each subsequent numbered entry.
    assert "\n\n2." in text


def test_render_trending_text_caps_at_eight_rows() -> None:
    tokens = [
        {"address": f"0x{i:040d}", "name": f"T{i}", "symbol": f"T{i}", "change_24h": i}
        for i in range(10)
    ]
    text = _render_trending_text(tokens, "ethereum", "24h")
    assert "\n9. " not in text  # numbering stops at 8
    assert "\n8. " in text


# ── keyboard builder ─────────────────────────────────────────────────────────


def test_build_trending_kb_buttons_and_selectors() -> None:
    tokens = [
        {"address": "0xAAAA", "name": "Pepe", "symbol": "PEPE"},
        {"address": "0xBBBB", "name": "", "symbol": ""},
    ]
    kb = _build_trending_kb(tokens, "base", "7d")
    rows = kb.inline_keyboard
    # Token buttons first: named label + scan callback.
    assert rows[0][0].callback_data == "scan_0xAAAA_base"
    assert "🔍 Pepe (PEPE)" in rows[0][0].text
    assert rows[1][0].callback_data == "scan_0xBBBB_base"
    assert "🔍 0xBBBB" in rows[1][0].text  # unknown -> truncated address label
    flat = [btn for row in rows for btn in row]
    callbacks = [btn.callback_data for btn in flat]
    assert "trending_chain_base" in callbacks
    assert "trending_win_base_7d" in callbacks
    assert "trending_refresh_base" in callbacks
    assert "menu_main" in callbacks
    # Current chain/window render bracketed among the selectors.
    base_btn = next(b for b in flat if b.callback_data == "trending_chain_base")
    assert base_btn.text.startswith("[")
    win_btn = next(b for b in flat if b.callback_data == "trending_win_base_7d")
    assert win_btn.text.startswith("[")
