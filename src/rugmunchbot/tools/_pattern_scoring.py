"""Numeric scoring + reporting for pattern recall (split out of pattern_memory).

Pure functions only: no Redis, no I/O. ``pattern_memory`` keeps the store and
the tool entry point; this module owns the signal cleaning, the fuzzy match
score, the top-N selection and the chat-facing summary.
"""

from __future__ import annotations

import math
from typing import Any

from rugmunchbot.tools.registry import ToolResult

_DEFAULT_TOP_K = 5
_DEFAULT_MIN_SIMILARITY = 0.5
_TOP_K_CEILING = 20


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_signals(signals: Any) -> dict[str, float]:
    """Keep only finite numeric values (never None/NaN/inf into the store)."""
    out: dict[str, float] = {}
    if not isinstance(signals, dict):
        return out
    for key, value in signals.items():
        f = _to_float(value)
        if f is not None and math.isfinite(f):
            out[str(key)] = f
    return out


def _similarity(query: dict[str, float], stored: dict[str, float]) -> float:
    """Fuzzy numeric-signal match over the intersection of signal keys.

    Per overlapping key the score is ``1 - relative_error`` (relative to the
    larger magnitude), so +200% vs -85% scores 0 (opposite directions) and
    +200% vs +210% scores ~0.95. 0.0 when no keys overlap.
    """
    keys = set(query) & set(stored)
    if not keys:
        return 0.0
    scores: list[float] = []
    for key in keys:
        a = query[key]
        b = stored[key]
        denom = max(abs(a), abs(b))
        if denom < 1e-9:
            scores.append(1.0 if abs(a - b) < 1e-9 else 0.0)
        else:
            scores.append(max(0.0, 1.0 - abs(a - b) / denom))
    return sum(scores) / len(scores)


def clamp_params(top_k: Any, min_similarity: Any) -> tuple[int, float]:
    """Clamp the caller's knobs to their legal ranges (defaults on garbage)."""
    try:
        top_k_i = max(1, min(int(top_k), _TOP_K_CEILING))
        min_sim = max(0.0, min(float(min_similarity), 1.0))
    except (TypeError, ValueError):
        return _DEFAULT_TOP_K, _DEFAULT_MIN_SIMILARITY
    return top_k_i, min_sim


def score_patterns(
    patterns: list[dict[str, Any]],
    query: dict[str, float],
    top_k: int,
    min_similarity: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Score every stored pattern; return the top matches + outcome counts."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for rec in patterns:
        stored_signals = _clean_signals(rec.get("signals"))
        if not stored_signals:
            continue
        score = _similarity(query, stored_signals)
        if score >= min_similarity:
            scored.append((score, rec))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    matches: list[dict[str, Any]] = []
    outcome_counts: dict[str, int] = {}
    for score, rec in scored[:top_k]:
        outcome = rec.get("outcome")
        label = ""
        if isinstance(outcome, dict):
            label = str(outcome.get("label") or "unknown")
        elif isinstance(outcome, str):
            label = outcome or "unknown"
        key = label or "unknown"
        outcome_counts[key] = outcome_counts.get(key, 0) + 1
        matches.append(
            {
                "token": rec.get("token"),
                "pattern": rec.get("pattern"),
                "signals": rec.get("signals"),
                "outcome": outcome,
                "detected_at": rec.get("detected_at"),
                "similarity": round(score, 3),
            }
        )
    return matches, outcome_counts


def recall_summary(
    matches: list[dict[str, Any]], outcome_counts: dict[str, int], stored_count: int
) -> str:
    """Chat-friendly one-liner: what was recalled, or that nothing matched."""
    if matches:
        parts = [
            f"{count}x {label}"
            for label, count in sorted(outcome_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        plural = "s" if len(matches) != 1 else ""
        return (
            f"{len(matches)} similar pattern{plural} seen before "
            f"(of {stored_count} in memory): {', '.join(parts)}"
        )
    plural = "s" if stored_count != 1 else ""
    return f"No similar patterns in memory ({stored_count} pattern{plural} stored, none matched)."


def empty_recall_result(
    address: str,
    symbol: str,
    chain: str,
    snapshot: dict[str, Any],
    stored_count: int,
) -> ToolResult:
    """Honest empty recall: a pair resolved but no numeric signals derivable."""
    return ToolResult(
        tool="pattern_recall",
        ok=True,
        data={
            "query": {"address": address, "symbol": symbol, "chain": chain},
            "store_available": True,
            "stored_count": stored_count,
            "matches": [],
            "outcome_distribution": {},
            "summary_text": (
                f"No comparable scan signals could be derived "
                f"({stored_count} pattern(s) stored, nothing compared)."
            ),
            "note": "pair resolved but no numeric snapshot signals; nothing compared",
        },
        symbols=[snapshot["symbol"]] if snapshot.get("symbol") else [],
        addresses=[snapshot["address"]] if snapshot.get("address") else [],
        source="pattern_memory:redis",
    )


__all__ = [
    "_clean_signals",
    "_similarity",
    "_to_float",
    "clamp_params",
    "empty_recall_result",
    "recall_summary",
    "score_patterns",
]
