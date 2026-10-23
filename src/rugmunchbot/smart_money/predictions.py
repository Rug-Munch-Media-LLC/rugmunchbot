"""Prediction-market odds feed (multi-source, aggregate only).

Polymarket and Kalshi are both aggregate books: Kalshi carries **no wallet
identity**, and neither source carries liquidations. An odds *shift* is only
shown when the payload actually supplies a delta — otherwise the post shows
the level and says no 24h baseline is available.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ._http import backend_json
from .types import Feed

_TRENDING = "/api/v1/prediction-markets/trending"
_DELTA = ("change_24h", "probability_change", "change_24h_pts", "delta")


def _yes(row: dict[str, Any]) -> float | None:
    prob = row.get("probability")
    raw = prob.get("yes") if isinstance(prob, dict) else row.get("yes_price")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, value))


def _delta(row: dict[str, Any]) -> Any:
    for key in _DELTA:
        value = row.get(key)
        if value not in (None, "", 0, 0.0):
            return value
    return None


def _row(row: dict[str, Any]) -> dict[str, Any] | None:
    question = row.get("question") or row.get("title")
    yes = _yes(row)
    if not question or yes is None:
        return None
    source = str(row.get("source") or "unknown").lower()
    return {
        "market_id": row.get("id") or row.get("conditionId") or row.get("slug") or question[:24],
        "question": str(question),
        "yes": yes,
        "volume_usd": row.get("volume_usd") or row.get("volume"),
        "source": source,
        "ends_at": row.get("ends_at") or row.get("endDate"),
        "delta": _delta(row),
        "wallet_identity": row.get("wallet") if source == "polymarket" else None,
    }


async def fetch_predictions(limit: int = 6) -> Feed:
    """Fetch normalised prediction-market odds, or an honest empty Feed."""
    body = await backend_json(_TRENDING, params={"limit": limit, "category": "all"})
    rows = [_row(r) for r in ((body or {}).get("results") or [])[: limit * 2]]
    items = tuple(r for r in rows if r)[:limit]
    if not items:
        return Feed(kind="predictions", note="prediction markets unavailable", tried=(_TRENDING,))
    sources = (body or {}).get("sources") or []
    return Feed(
        kind="predictions",
        items=items,
        source=" + ".join(str(s) for s in sources) or "prediction-markets",
        updated_at=str((body or {}).get("updated_at") or datetime.now(UTC).isoformat()),
        tried=(_TRENDING,),
    )
