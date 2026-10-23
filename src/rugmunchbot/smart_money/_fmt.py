"""Formatting primitives shared by the smart-money renderers."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any


def esc(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=False)


def usd(value: Any) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(n) >= 1e9:
        return f"${n / 1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n / 1e6:.2f}M"
    if abs(n) >= 1e3:
        return f"${n / 1e3:.1f}K"
    return f"${n:,.0f}"


def stamp(value: Any) -> str:
    """ISO timestamp → ``2026-10-23 10:14Z``; ``unknown`` when unparseable."""
    if not value:
        return "unknown"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%MZ")


def short(value: Any, head: int = 6, tail: int = 4) -> str:
    text = str(value or "")
    return f"{text[:head]}…{text[-tail:]}" if len(text) > head + tail else text


def pts(value: Any) -> str:
    """Probability delta in points, only when the feed actually supplied one."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{n * 100:+.1f} pts" if abs(n) < 1 else f"{n:+.1f} pts"
