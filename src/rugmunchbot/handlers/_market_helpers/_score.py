"""Per-token score line for trending cards — engine-provided score + evidence.

The number is the engine's ``score`` (``rugbrain.trending_fusion/v1``). This
module only formats it and lists the engine's own ``evidence`` drivers; it
never computes a score. Paid placement is called out on the line, per the
disclosure rule that paid tokens are shown but never silently ranked.
"""

from __future__ import annotations

from typing import Any


def _score_metrics(token: dict[str, Any]) -> dict[str, Any]:
    """Shape the engine evidence for the card; no arithmetic, no re-ranking."""
    evidence = token.get("evidence") or []
    return {
        "score": token.get("score"),
        "paid": bool(token.get("paid")),
        "drivers": [
            f"{e.get('source')}:{e.get('signal')}" for e in evidence if isinstance(e, dict)
        ][:3],
    }


def score_line(token: dict[str, Any]) -> str:
    """Render ``score 0.73 · dexscreener:volume_24h · paid`` for one token."""
    metrics = _score_metrics(token)
    score = metrics["score"]
    parts = ["score —" if score is None else f"score {float(score):.2f}"]
    if metrics["drivers"]:
        parts.append(" · ".join(metrics["drivers"]))
    if metrics["paid"]:
        parts.append("paid")
    return " · ".join(parts)


def _score_line(token: dict[str, Any]) -> str:
    """Compat alias exported by ``_market_helpers`` — engine score line, no compute."""
    return score_line(token)
