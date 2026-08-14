"""RugMunchBot — Service layer.

Re-exports the visuals engine functions so consumers can import them from
the package root (``from rugmunchbot.services import render_cluster_map``).
Ported from the live production copy.
"""

from __future__ import annotations

from rugmunchbot.services.visuals import (
    render_candlestick_chart,
    render_cluster_map,
)

__all__ = ["render_candlestick_chart", "render_cluster_map"]
