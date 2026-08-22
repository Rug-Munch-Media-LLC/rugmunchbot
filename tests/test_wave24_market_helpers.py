"""Test wave 24 — market command pure helpers (USD fmt, movers rendering).

Contracts read from source (2026-08-22). Pure functions only.
"""

from __future__ import annotations

from rugmunchbot.formatters import dexscreener_url
from rugmunchbot.handlers.market import (
    _TRENDING_KEY_PREFIX,
    _TRENDING_TTL,
    MARKET_UNAVAILABLE,
    _change_emoji,
    _fmt_usd,
    _mover_link,
    _render_movers_lines,
)

# ── USD formatting tiers ─────────────────────────────────────────────────────


def test_fmt_usd_tiers() -> None:
    assert _fmt_usd(3.2e12) == "$3.20T"
    assert _fmt_usd(2.5e9) == "$2.50B"
    assert _fmt_usd(5e6) == "$5.00M"
    assert _fmt_usd(67_234.5) == "$67,234.50"
    assert _fmt_usd(3.5) == "$3.5000"
    # Sub-cent tier renders six decimals despite the stale docstring example.
    assert _fmt_usd(0.05) == "$0.050000"
    assert _fmt_usd(0.00001234) == "$0.0000123400"


def test_fmt_usd_none_and_junk() -> None:
    assert _fmt_usd(None) == "N/A"
    assert _fmt_usd("junk") == "junk"  # falls back to str(n)


def test_change_emoji_sign_gate() -> None:
    assert _change_emoji(0) == "\U0001f7e2"  # zero counts as green
    assert _change_emoji(1.5) == "\U0001f7e2"
    assert _change_emoji(-0.1) == "\U0001f534"


def test_trending_cache_constants() -> None:
    assert _TRENDING_TTL == 90
    assert _TRENDING_KEY_PREFIX == "trending:"
    assert isinstance(MARKET_UNAVAILABLE, str)
    assert MARKET_UNAVAILABLE


# ── mover links ──────────────────────────────────────────────────────────────


def test_mover_link_gmgn_uses_dexscreener() -> None:
    out = _mover_link({"source": "gmgn", "address": "0xabc", "chain": "ETH"})
    assert out == dexscreener_url("0xabc", "eth")


def test_mover_link_exchange_entries_use_coingecko() -> None:
    assert _mover_link({"address": "bitcoin"}) == "https://www.coingecko.com/en/coins/bitcoin"
    assert _mover_link({}) == "https://www.coingecko.com/"


# ── movers message body (pure renderer) ──────────────────────────────────────

_GAINER = {
    "symbol": "pepe",
    "name": "Pepe Coin",
    "price": 0.001,
    "volume_24h": 1_000_000,
    "change_24h": 5.0,
    "price_change_24h": 5.0,
    "source": "GMGN",
    "address": "0xabc",
    "chain": "eth",
    "is_new_launch": True,
}


def test_render_movers_lines_structure_and_badges() -> None:
    lines = _render_movers_lines([_GAINER], [], "24H", sources=["gmgn", "cg"])
    text = "\n".join(lines)
    assert "Market Movers" in text
    assert "📡 Sources: gmgn, cg" in text
    assert "🟢 <b>TOP GAINERS</b>" in text
    assert "<b>Pepe Coin</b> (PEPE)" in text
    assert "(GMGN)" in text  # source tag on the gainer row
    assert "🆕" in text  # new-launch badge
    assert "Chart</a>" in text
    assert "🔴 <b>TOP LOSERS</b>" in text
    assert "market is green" in text  # empty-losers honest fallback


def test_render_movers_lines_no_sources_line_when_absent() -> None:
    lines = _render_movers_lines([], [{"symbol": "doge", "change_24h": -3}], "24H")
    text = "\n".join(lines)
    assert "Sources:" not in text
    assert "<i>No gainers in the current window.</i>" in text
    assert "DOGE" in text  # symbol uppercased, capped at 12 chars by contract
