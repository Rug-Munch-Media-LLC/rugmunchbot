# VENDORED 2026-08-17 from rmi-backend/app/osint/dex_activity/_common.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Shared helpers for the wash-trading detector (``app.osint.dex_activity``).

Holds the small pure functions used by both the signal heuristics
(:mod:`app.osint.dex_activity._signals`) and the fetch/orchestration layer
(:mod:`app.osint.dex_activity._core`): numeric coercion, pair labeling, age
computation and finding emission.
"""

from __future__ import annotations

import time
from typing import Any, cast

from rugmunchbot._vendor.osint import OsintFinding, OsintReport


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce a DexScreener numeric field (float, int or str) to ``float``."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _subdict(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``mapping[key]`` when it is a dict, else an empty dict."""
    value = mapping.get(key)
    if not isinstance(value, dict):
        return {}
    return cast("dict[str, Any]", value)


def _token_symbol(token: Any) -> str:
    """Short symbol/name for a base/quote token object."""
    if not isinstance(token, dict):
        return "?"
    name = cast("Any", token.get("symbol") or token.get("name") or "?")
    return str(name)


def _fmt_usd(value: float) -> str:
    """Compact USD formatting for summaries (e.g. ``$1.2M``, ``$50.3K``)."""
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"${value / 1e6:.2f}M"
    if value >= 1e3:
        return f"${value / 1e3:.1f}K"
    return f"${value:.0f}"


def _pair_label(pair: dict[str, Any]) -> str:
    """Short human label for a pair: ``chain/dex/addr-prefix``."""
    chain = str(pair.get("chainId") or "?")
    dex = str(pair.get("dexId") or "?")
    addr = str(pair.get("pairAddress") or "?")
    return f"{chain}/{dex}/{addr[:10]}"


def _pair_age_hours(pair: dict[str, Any]) -> float | None:
    """Age of the pair in hours from ``pairCreatedAt`` (ms), else ``None``."""
    created_ms = _num(pair.get("pairCreatedAt"))
    if created_ms <= 0:
        return None
    return max(0.0, (time.time() * 1000.0 - created_ms) / 3_600_000.0)


def _add_finding(
    report: OsintReport,
    source: str,
    category: str,
    summary: str,
    *,
    confidence: str = "medium",
    **data: Any,
) -> None:
    """Append a finding with an explicit confidence (same helper as wallet.py)."""
    report.findings.append(
        OsintFinding(
            source=source,
            category=category,
            summary=summary,
            confidence=confidence,
            data=data,
        )
    )
