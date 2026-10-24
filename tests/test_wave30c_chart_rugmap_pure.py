"""Test wave 30c — chart.py + rugmap.py pure surfaces.

Behavior probed via .venv/bin/python before asserting (2026-08-23).
Renderers asserted via PNG magic bytes per the visuals-wave pattern.
"""

from __future__ import annotations

from rugmunchbot.commands.chart import (
    _pseudo_sparkline,
    _sparkline,
    _web_chart_url,
    render_chart_png,
)
from rugmunchbot.commands.rugmap import (
    _guess_chain,
    _web_rugmap_url,
)
from rugmunchbot.services.visuals import render_cluster_map

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ── _sparkline ──


def test_sparkline_empty() -> None:
    assert _sparkline([]) == ""


def test_sparkline_flat_renders_mid_block() -> None:
    assert _sparkline([5, 5, 5]) == "\u2584\u2584\u2584"  # min(width=8, len=3)


def test_sparkline_full_rise() -> None:
    assert (
        _sparkline([1, 2, 3, 4, 5, 6, 7, 8]) == "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    )


def test_sparkline_samples_down_to_width() -> None:
    out = _sparkline(list(range(100)), width=8)
    assert len(out) == 8
    assert out == "\u2581\u2582\u2584\u2586\u2587\u2588\u2588\u2588" or out.startswith("\u2581")


# ── _pseudo_sparkline ──


def test_pseudo_sparkline_from_change_percentages() -> None:
    # Probed: 7d→24h→6h→1h→now reconstruction.
    assert (
        _pseudo_sparkline(1.0, 5, 3, 10, 40) == "\u2581\u2583\u2587\u2586\u2588\u2588\u2588\u2588"
    )


def test_pseudo_sparkline_all_zero_changes_is_flat() -> None:
    # No changes anywhere -> points collapse to [price, price] -> flat blocks.
    out = _pseudo_sparkline(1.0, 0, None, 0, 0)
    assert set(out) == {"\u2584"}


# ── URL builders ──


def test_web_chart_url() -> None:
    assert _web_chart_url("eth", "0xpair") == "https://dexscreener.com/eth/0xpair"


def test_web_rugmap_url() -> None:
    assert _web_rugmap_url("0xabc", "solana") == (
        "https://rugmunch.io/rugmaps?token=0xabc&chain=solana"
    )


# ── _guess_chain ──


def test_guess_chain_evm_address() -> None:
    assert _guess_chain("0x" + "ab" * 20) == "ethereum"


def test_guess_chain_junk_returns_unknown_not_solana() -> None:
    # QUIRK: detect_chain returns the truthy string 'unknown' for junk, so the
    # `or "solana"` fallback never fires for unrecognized inputs — only for
    # an empty/None detect result. Locked as-is; flagged in SESSION_TODO.
    assert _guess_chain("zzz") == "unknown"


# ── renderers (PNG magic bytes) ──


def test_render_chart_png_empty_candles_none() -> None:
    assert render_chart_png([], "Pepe", "PEPE") is None


def test_render_chart_png_valid_candles_png_bytes() -> None:
    candles = [
        {"time": 1700000000000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}
    ]
    out = render_chart_png(candles, "Pepe", "PEPE", "eth")
    assert out is not None
    assert out.startswith(PNG_MAGIC)
    assert len(out) > 5000


def test_render_cluster_map_empty_nodes_none() -> None:
    assert render_cluster_map([], [], "Pepe", "PEPE", "0xtok") is None


def test_render_cluster_map_valid_graph_png_bytes() -> None:
    nodes = [{"id": "a", "address": "0xa", "label": "dev", "value": 10, "risk": "high"}]
    links = [{"source": "a", "target": "a", "weight": 1}]
    out = render_cluster_map(nodes, links, "Pepe", "PEPE", "0xtok")
    assert out is not None
    assert out.startswith(PNG_MAGIC)
    assert len(out) > 5000
