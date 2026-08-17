# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/insider_detector/helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Base helpers — no circular dependencies."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _parse_timestamp(ts: Any) -> float | None:
    """Parse a timestamp from various formats."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            return ts / 1000
        return float(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, AttributeError):
            pass
    return None


def _ts_human(ts: float) -> str:
    """Format a Unix timestamp to a human-readable UTC string."""
    if not ts:
        return "unknown"
    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return str(ts)
