"""ct_shill_watch — Qdrant ``social_intel`` embedding for shill records.

Visibility (2026-10-25): the shill upsert used to fail-open at ``debug`` level,
so a dead embedder or a rejected Qdrant write was invisible in production logs.
Every failure now returns a typed ``EmbedResult`` with a machine ``reason`` and
logs a structured ``warning``; ``embed_shill`` keeps its ``bool`` contract for
existing callers.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger

from . import _config
from ._wall import _norm_handle

logger = get_logger("rmi_bot.channels.ct_shill_watch")


@dataclass(frozen=True)
class EmbedResult:
    """Typed shill-upsert outcome — ``ok=False`` always carries a reason."""

    ok: bool = False
    reason: str = ""
    point_id: str = ""
    dims: int = 0


def _point_id(handle: str) -> uuid.UUID:
    """Deterministic point id per handle (stable across re-runs)."""
    digest = hashlib.sha256(f"shill:{handle}".encode()).hexdigest()
    return uuid.UUID(int=int(digest, 16) % (2**96), version=4)


def _payload(record: dict[str, Any], handle: str, digest: str) -> dict[str, Any]:
    """Qdrant payload for one shill record."""
    return {
        "doc_id": f"shill:{digest[:20]}",
        "kind": "kol_shill",
        "handle": handle,
        "handle_id": record.get("handle_id") or handle,
        "wallet": record.get("wallet") or "",
        "tokens_shilled": (record.get("tokens_shilled") or [])[:20],
        "shill_count": record.get("shill_count"),
        "coordinated": bool(record.get("coordinated")),
        "severity": record.get("severity") or "",
        "evidence_url": record.get("evidence_url") or "",
        "last_shill_ts": record.get("last_shill_ts") or "",
        "captured_at": _config.now_iso(),
    }


def _content(record: dict[str, Any], handle: str) -> str:
    """Canonical shill text that gets embedded."""
    tokens = ", ".join(str(t) for t in (record.get("tokens_shilled") or [])[:10])
    return (
        f"Shill: {handle}\nTokens shilled: {tokens}\n"
        f"Shill count: {record.get('shill_count')}\nCoordinated: {record.get('coordinated')}\n"
        f"Evidence: {record.get('evidence_url')}"
    ).strip()


async def embed_shill_result(record: dict[str, Any]) -> EmbedResult:
    """Upsert one shill record into Qdrant ``social_intel``; typed outcome.

    Direct upsert (same pattern as ``shill_watch._persist_shill_evidence`` —
    the logical-collection resolver can mis-redirect, so we hit the canonical
    endpoint with the physical name). Fail-open, but never silent.
    """
    handle = _norm_handle(record.get("handle"))
    if not handle:
        logger.warning("ct_shill:embed_skipped reason=no_handle")
        return EmbedResult(reason="no_handle")
    try:
        from rugmunchbot.channels.telegram_news._dedup import _embed_candidate

        vector = await _embed_candidate(_content(record, handle))
        if not vector:
            logger.warning("ct_shill:embed_skipped handle=%s reason=empty_vector", handle)
            return EmbedResult(reason="empty_vector")
        digest = hashlib.sha256(f"shill:{handle}".encode()).hexdigest()
        point_id = _point_id(handle)
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(
                f"{_config.QDRANT_URL}/collections/{_config.SOCIAL_INTEL_COLLECTION}/points",
                params={"wait": True},
                json={
                    "points": [
                        {
                            "id": str(point_id),
                            "vector": vector,
                            "payload": _payload(record, handle, digest),
                        }
                    ]
                },
            )
        if resp.status_code not in (200, 202):
            logger.warning(
                "ct_shill:qdrant_rejected handle=%s status=%d body=%s",
                handle,
                resp.status_code,
                resp.text[:160],
            )
            return EmbedResult(
                reason=f"qdrant_http_{resp.status_code}", point_id=str(point_id), dims=len(vector)
            )
        return EmbedResult(ok=True, point_id=str(point_id), dims=len(vector))
    except Exception as exc:  # fail-open by contract, but LOUD
        logger.warning(
            "ct_shill:embed_failed handle=%s err=%s",
            handle or "?",
            f"{type(exc).__name__}:{str(exc)[:140]}",
        )
        return EmbedResult(reason=f"embed_failed:{type(exc).__name__}")


async def embed_shill(record: dict[str, Any]) -> bool:
    """Upsert one shill record; ``False`` on any failure (never raises)."""
    return (await embed_shill_result(record)).ok


__all__ = ["EmbedResult", "embed_shill", "embed_shill_result"]
