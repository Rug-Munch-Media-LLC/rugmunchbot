"""ct_shill_watch — Qdrant ``social_intel`` embedding for shill records."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger

from . import _config
from ._wall import _norm_handle

logger = get_logger("rmi_bot.channels.ct_shill_watch")


async def embed_shill(record: dict[str, Any]) -> bool:
    """Upsert one shill record into the Qdrant ``social_intel`` collection.

    Direct upsert (same pattern as ``shill_watch._persist_shill_evidence`` —
    the logical-collection resolver can mis-redirect, so we hit the canonical
    endpoint with the physical name). Deterministic point id per handle.
    Fail-open False.
    """
    handle = _norm_handle(record.get("handle"))
    if not handle:
        return False
    try:
        from rugmunchbot.channels.telegram_news._dedup import _embed_candidate

        tokens = ", ".join(str(t) for t in (record.get("tokens_shilled") or [])[:10])
        content = (
            f"Shill: {handle}\nTokens shilled: {tokens}\n"
            f"Shill count: {record.get('shill_count')}\nCoordinated: {record.get('coordinated')}\n"
            f"Evidence: {record.get('evidence_url')}"
        ).strip()
        vector = await _embed_candidate(content)
        if not vector:
            return False
        digest = hashlib.sha256(f"shill:{handle}".encode()).hexdigest()
        point_id = uuid.UUID(int=int(digest, 16) % (2**96), version=4)
        payload = {
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
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.put(
                f"{_config.QDRANT_URL}/collections/{_config.SOCIAL_INTEL_COLLECTION}/points",
                params={"wait": True},
                json={"points": [{"id": str(point_id), "vector": vector, "payload": payload}]},
            )
        ok = resp.status_code in (200, 202)
        if not ok:
            logger.debug("ct_shill:qdrant_status=%d body=%s", resp.status_code, resp.text[:160])
        return ok
    except Exception as exc:
        logger.debug("ct_shill:embed_failed err=%s", str(exc)[:160])
        return False
