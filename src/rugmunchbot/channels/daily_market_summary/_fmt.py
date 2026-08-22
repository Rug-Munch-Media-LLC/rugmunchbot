"""Number/percent/volume formatting for the daily summary."""

from __future__ import annotations

from typing import Any


def _fmt_price(value: Any) -> str:
    """USD price or 'N/A' — never invents a number."""
    if value is None or value == "" or value == 0:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.4f}"


def _fmt_pct(value: Any) -> str:
    """Signed 24h % with arrows; missing → 'N/A'."""
    if value is None or value == "":
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v > 0:
        return f"+{v:.1f}%"
    if v < 0:
        return f"{v:.1f}%"
    return "0.0%"


def _fmt_volume(value: Any) -> str:
    """Compact USD volume (K/M/B) or 'N/A'."""
    if value is None or value == "" or value == 0:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if abs(v) >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"
