"""Qdrant point search + hit mapping for the direct grounding lane.

Split out of ``rag_qdrant`` (200-line gate). Owns ONE collection search and the
point -> fleet-hit shape; the embed and the multi-collection fan-out stay in
``rag_qdrant``. Every failure returns a typed ``LaneOutcome(degraded=True)``
and logs a structured warning — a missing collection is never silent.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.rag_outcomes import LaneOutcome

logger = get_logger("rmi_bot.tools.rag_qdrant")


def _first(payload: dict[str, Any], *keys: str) -> str:
    """First non-empty trimmed value among *keys* (branch-flat helper)."""
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _hit(point: dict[str, Any], collection: str) -> dict[str, Any] | None:
    """Map one Qdrant point to the fleet hit shape (None when empty)."""
    pl = point.get("payload") or {}
    content = _first(pl, "content", "text")
    if not content:
        return None
    meta = pl.get("metadata")
    return {
        "content": content,
        "score": float(point.get("score") or 0.0),
        "collection": pl.get("collection") or collection,
        "metadata": meta if isinstance(meta, dict) else {},
        "id": _first(pl, "doc_id") or str(point.get("id") or ""),
    }


async def collection_search_outcome(
    collection: str, vector: list[float], limit: int
) -> LaneOutcome:
    """Vector-search one collection; a failure is typed as ``degraded``."""
    from rugmunchbot.tools.rag_qdrant import qdrant_url

    url = f"{qdrant_url()}/collections/{collection}/points/search"
    payload = {"vector": vector, "limit": limit, "with_payload": True}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0, read=6.0, write=6.0)
        ) as client:
            resp = await asyncio.wait_for(
                client.post(url, headers={"Content-Type": "application/json"}, json=payload),
                timeout=6.0,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError) as exc:
        logger.warning(
            "rag_qdrant:collection_unavailable collection=%s err=%s",
            collection,
            type(exc).__name__,
        )
        return LaneOutcome(
            ok=False, degraded=True, reason=f"search_unavailable:{type(exc).__name__}"
        )
    if resp.status_code != 200:
        logger.warning(
            "rag_qdrant:collection_http collection=%s status=%s", collection, resp.status_code
        )
        return LaneOutcome(ok=False, degraded=True, reason=f"search_http_{resp.status_code}")
    try:
        raw = resp.json()
    except ValueError:
        return LaneOutcome(ok=False, degraded=True, reason="search_bad_body")
    result = raw.get("result") if isinstance(raw, dict) else None
    points = result if isinstance(result, list) else []
    hits = [h for h in (_hit(p, collection) for p in points if isinstance(p, dict)) if h]
    return LaneOutcome(hits=hits, ok=True)


async def collection_search(
    collection: str, vector: list[float], limit: int
) -> list[dict[str, Any]]:
    """Vector-search one Qdrant collection. Returns ``[]`` on failure (never raises)."""
    return (await collection_search_outcome(collection, vector, limit)).hits


__all__ = ["collection_search", "collection_search_outcome"]
