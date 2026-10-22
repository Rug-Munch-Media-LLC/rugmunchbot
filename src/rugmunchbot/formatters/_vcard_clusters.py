"""Per-cohort cluster lines for the scan card.

Rule 3: each launch cohort shows ``bought -> current -> realised SOL`` with
its wallet count, so a reader sees both size and whether the cohort is still
holding. A missing cohort renders ``—`` with the engine's own reason.

One public entry point: :func:`cluster_lines`.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._vcard_fields import DASH


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return DASH


def _sol(value: Any) -> str:
    try:
        return f"{float(value):.2f} SOL"
    except (TypeError, ValueError):
        return f"{DASH} SOL"


def cluster_lines(r: dict[str, Any], ts: str) -> list[str]:
    """Rule 3: per-cohort bought -> current -> realised SOL, with counts."""
    cohorts = r.get("cohorts") if isinstance(r.get("cohorts"), dict) else None
    if not cohorts or cohorts.get("reason"):
        reason = cohorts.get("reason") if cohorts else "cohort engine not run"
        return [f"Clusters  {DASH} \u00b7 engine: {reason}"]
    lines = [f"Clusters  \u00b7 engine \u00b7 {ts}"]
    for key, label in (("team", "Team"), ("insiders", "Insiders"), ("snipers", "Snipers")):
        block = cohorts.get(key)
        if not isinstance(block, dict):
            lines.append(f"  {label} {DASH}")
            continue
        count = block.get("count")
        count_str = f"{int(count):,}" if isinstance(count, (int, float)) else DASH
        lines.append(
            f"  {label} {count_str}: {_pct(block.get('initial_buy_pct'))} \u2192 "
            f"{_pct(block.get('current_pct'))} \u00b7 {_sol(block.get('realized_sol'))}"
        )
    return lines
