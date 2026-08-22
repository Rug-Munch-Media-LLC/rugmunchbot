"""Wave 8 — visuals: branded chart/map renderers.

Contracts probed against the implementation before asserting (oracle policy):
guard rails return None (empty/too-few candles, malformed rows, no nodes);
successful renders return non-trivial bytes starting with the PNG magic.
"""

from __future__ import annotations

import pytest

from rugmunchbot.services.visuals import render_candlestick_chart, render_cluster_map

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _candles(n: int) -> list[dict[str, float]]:
    return [
        {
            "time": i,
            "open": 100.0 + i,
            "high": 102.0 + i,
            "low": 99.0 + i,
            "close": 101.0 + i,
            "volume": 10.0 + i,
        }
        for i in range(n)
    ]


# ── candlestick chart ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad", [[], [{"close": 1.0}, {"close": 2.0}]])
def test_chart_guards_return_none(bad: list[dict[str, float]]) -> None:
    assert render_candlestick_chart(bad, "Tok", "TKN") is None


def test_chart_renders_png_bytes() -> None:
    out = render_candlestick_chart(
        _candles(30), "Test Token", "TST", chain="solana", timeframe="1h"
    )
    assert isinstance(out, bytes)
    assert out.startswith(PNG_MAGIC)
    assert len(out) > 10_000


def test_chart_all_malformed_rows_none() -> None:
    rows = [{"close": "x"}, {"nope": 1}]
    assert render_candlestick_chart(rows, "B", "B") is None


def test_chart_flat_series_still_renders() -> None:
    flat = [{"close": 5.0, "high": 5.0, "low": 5.0, "volume": 0} for _ in range(10)]
    out = render_candlestick_chart(flat, "F", "F")
    assert out is not None
    assert out.startswith(PNG_MAGIC)


def test_chart_rsi_and_no_volume_paths_render() -> None:
    rsi_on = render_candlestick_chart(_candles(30), "T", "T", show_rsi=True)
    vol_off = render_candlestick_chart(_candles(30), "T", "T", show_volume=False)
    assert rsi_on is not None
    assert vol_off is not None
    assert rsi_on.startswith(PNG_MAGIC)
    assert vol_off.startswith(PNG_MAGIC)


def test_chart_short_candle_keys_accepted() -> None:
    short = [{"c": 1.0 + i, "h": 2.0 + i, "l": 0.5 + i, "v": 3.0} for i in range(12)]
    out = render_candlestick_chart(short, "S", "S")
    assert out is not None
    assert out.startswith(PNG_MAGIC)


# ── cluster map ─────────────────────────────────────────────────────────


def test_map_empty_nodes_none() -> None:
    assert render_cluster_map([], [], "N", "N") is None


def test_map_renders_png_with_layout_fallback() -> None:
    # No x/y on nodes → circular fallback layout must still render.
    nodes = [
        {"address": "A" * 44, "label": "binance", "risk": 80},
        {"address": "B" * 44, "label": "whale", "risk_level": "low", "weight": 5},
        {"address": "C" * 44},
    ]
    links = [
        {"source": "A" * 44, "target": "B" * 44},  # known pair drawn
        {"source": "X", "target": "Y"},  # unknown endpoints ignored
    ]
    out = render_cluster_map(
        nodes, links, "Tok", "TKN", chain="solana", total_holders=1234, top10_pct=41.2
    )
    assert isinstance(out, bytes)
    assert out.startswith(PNG_MAGIC)
    assert len(out) > 10_000


def test_map_dict_endpoints_and_explicit_positions() -> None:
    nodes = [
        {"id": "n1", "fx": 0.0, "fy": 0.0, "risk_score": 65},
        {"id": "n2", "x": 1.0, "y": 1.0, "value": 9},
    ]
    links = [{"source": {"id": "n1"}, "target": {"id": "n2"}}]  # dict endpoints resolved
    out = render_cluster_map(nodes, links, "M", "M")
    assert out is not None
    assert out.startswith(PNG_MAGIC)
