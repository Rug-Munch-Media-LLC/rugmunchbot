# VENDORED 2026-08-24 from rmi-backend/app/rag/compat.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Compat shims — all RAG ops now route through rugbrain HTTP API.

All function signatures preserved for backward compatibility.
Underlying implementation uses ``app.integrations.rugbrain_client``.

REMOVAL 2026-08-15: delete this file after all callers migrate to
``app.integrations.rugbrain_client`` directly.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)


# ── Redis utility ──────────────────────────────────────────────────────────

_redis_pool: Any = None


async def _get_redis() -> Any:
    """Get async Redis connection (preserved stub)."""
    import redis.asyncio as aioredis

    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://rmi-redis:6379/0"),
            decode_responses=True,
        )
    return _redis_pool


# ── Search functions ───────────────────────────────────────────────────────


async def search_similar(
    query: str,
    collection: str,
    limit: int = 10,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Search a single collection via rugbrain HTTP API."""
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    return await rugbrain_search(query=query, collections=[collection], limit=limit)


async def search_multi_collection(
    query: str,
    collections: list[str],
    limit: int = 10,
    min_similarity: float = 0.0,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Search multiple collections via rugbrain HTTP API.

    Returns results list (same shape as old compat layer).
    """
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    return await rugbrain_search(query=query, collections=collections, limit=limit)


async def three_pillar_search(
    query: str,
    collections: list[str] | None = None,
    collection: str = "",
    limit: int = 10,
    top_k: int = 5,
    min_similarity: float = 0.0,
    **kwargs: Any,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Legacy three-pillar search — routes through rugbrain HTTP API.

    Accepts both ``collection`` (singular, old style) and ``collections``
    (list, new style). Returns ``{"results": [...]}`` dict for callers
    that expect the old shape, or plain list for callers that expect a list.
    """
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    colls: list[str] = collections or ([collection] if collection else ["threat_intel_v2"])
    results = await rugbrain_search(query=query, collections=colls, limit=max(limit, top_k))
    # Return dict shape for callers that expect .get("results")
    return {"results": results}


async def search_documents(
    collection: str,
    query: str,
    limit: int = 5,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Search documents in a single collection (old signature)."""
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    return await rugbrain_search(query=query, collections=[collection], limit=limit)


async def detect_scam_patterns(token_data: dict[str, Any], threshold: float = 0.65) -> dict[str, Any]:
    """Compare token against known scam patterns via rugbrain + keyword matching.

    Preserves the old return shape: ``quick_matches`` / ``deep_matches`` /
    ``highest_threat`` / ``threat_severity``.
    """
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    quick_matches: list[dict[str, Any]] = []
    deep_matches: list[dict[str, Any]] = []

    # 1. Quick code-snippet keyword matches
    try:
        from rugmunchbot._vendor.crypto_embeddings import KNOWN_SCAM_PATTERNS

        code_lower = (token_data.get("contract_code") or "").lower()
        for pattern in KNOWN_SCAM_PATTERNS:
            snippets = pattern.get("code_snippets") or []
            if not snippets:
                continue
            quick_hits = sum(1 for s in snippets if s.lower() in code_lower)
            if quick_hits > 0:
                quick_matches.append(
                    {
                        "pattern": pattern["name"],
                        "severity": pattern.get("severity", "high"),
                        "quick_match_score": quick_hits / len(snippets),
                        "matched_snippets": quick_hits,
                    }
                )
    except Exception as e:
        logger.debug("detect_scam_quick_failed err=%s", str(e)[:120])

    # 2. Deep semantic matches via rugbrain search
    try:
        query = f"{token_data.get('address', '')} {token_data.get('symbol', '')} {token_data.get('name', '')} scam pattern".strip()
        hits = await rugbrain_search(
            query=query, collections=["known_scams", "scam_patterns", "defi_exploits"], limit=5
        )
        for h in hits:
            score = float(h.get("rrf_score", 0) or 0)
            meta = h.get("metadata", {}) or {}
            if score >= 0.005:  # RRF scores are ~0.01-0.02, so 0.005 catches anything meaningful
                deep_matches.append(
                    {
                        "pattern": meta.get("pattern_name", meta.get("name", "")),
                        "severity": meta.get("severity", "high"),
                        "description": (h.get("content") or h.get("text") or "")[:300],
                        "similarity": round(score, 4),
                    }
                )
    except Exception as e:
        logger.debug("detect_scam_deep_failed err=%s", str(e)[:120])

    deep_matches.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    return {
        "quick_matches": quick_matches,
        "deep_matches": deep_matches,
        "highest_threat": deep_matches[0]["pattern"] if deep_matches else "none",
        "threat_severity": deep_matches[0]["severity"] if deep_matches else "low",
    }


# ── Ingestion functions ────────────────────────────────────────────────────


async def ingest_document(
    collection: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    doc_id: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Ingest a document via rugbrain HTTP API."""
    from rugmunchbot._vendor.rugbrain_client import rugbrain_ingest

    ok = await rugbrain_ingest(
        content=content,
        collection=collection,
        doc_id=doc_id,
        metadata=metadata or {},
    )
    return {"status": "ok" if ok else "failed", "doc_id": doc_id or ""}


async def index_document(
    collection: str,
    doc_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Index a document via rugbrain HTTP API (alias for ingest)."""
    return await ingest_document(collection=collection, content=text, doc_id=doc_id, metadata=metadata)
