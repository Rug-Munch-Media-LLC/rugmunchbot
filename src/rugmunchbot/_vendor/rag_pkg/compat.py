# VENDORED 2026-08-17 from rmi-backend/app/rag/compat.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Deprecated-compat shims for ``app.rag_service`` consumers (REMOVAL 2026-08-15).

These functions preserve the OLD ``app.rag_service`` call signatures and
return shapes so existing callers can migrate off ``app.rag_service``
without changing their call sites. They are thin wrappers over the
v3 M3 engine (``app.rag``).

⚠️  DEPRECATED — do not use in new code. Prefer the native ``app.rag`` API:
    - ``three_pillar_search``  → ``app.rag.engine.three_pillar_search()``
    - ``search_similar``       → ``app.rag.hybrid_search()``
    - ``search_multi_collection`` → ``app.rag.HybridRetriever().retrieve_multi()``
    - ``detect_scam_patterns`` → ``app.rag.intelligence.RAGIntelligenceEnricher()``
    - ``ingest_document``      → ``app.rag.engine.ingest_document()``
    - ``get_embedder``         → ``app.rag.embedder.get_default_embedder()``
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Redis (async) — mirrors old app.rag_service.cache._get_redis ──────────

_redis_pool: Any = None


async def _get_redis() -> Any:
    """Get or create a shared async Redis connection (singleton)."""
    global _redis_pool
    import redis.asyncio as redis

    if _redis_pool is not None:
        try:
            await _redis_pool.ping()
        except (ConnectionError, OSError):
            _redis_pool = None
    if _redis_pool is None:
        _redis_pool = redis.Redis(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", "") or None,
            db=0,
            decode_responses=True,
        )
    return _redis_pool


# ── Embedder — re-export the active crypto embedder (not deprecated) ─────

from app.crypto_embeddings import get_embedder  # noqa: E402 — module docstring precedes import block

# ── Search shims ───────────────────────────────────────────────────────────

_DEFAULT_COLLECTIONS: list[str] = [
    "scam_intel",
    "deployer_history",
    "wallet_labels",
    "contract_audit",
    "phishing_db",
]


def _hit_to_dict(hit: Any, collection: str = "") -> dict[str, Any]:
    """Convert a new-engine ScoredHit (or engine hit dict) to old shape."""
    if isinstance(hit, dict):
        return {
            "doc_id": hit.get("doc_id", ""),
            "content": hit.get("text", hit.get("content", "")),
            "text": hit.get("text", hit.get("content", "")),
            "similarity": float(hit.get("score", hit.get("similarity", 0.0)) or 0.0),
            "score": float(hit.get("score", hit.get("similarity", 0.0)) or 0.0),
            "collection": hit.get("collection", collection),
            "metadata": hit.get("metadata", {}),
        }
    # ScoredHit dataclass
    return {
        "doc_id": getattr(hit, "doc_id", ""),
        "content": getattr(hit, "content", ""),
        "text": getattr(hit, "content", ""),
        "similarity": float(getattr(hit, "final_score", getattr(hit, "score", 0.0)) or 0.0),
        "score": float(getattr(hit, "final_score", getattr(hit, "score", 0.0)) or 0.0),
        "collection": getattr(hit, "collection", collection),
        "metadata": getattr(hit, "metadata", {}),
    }


async def search_similar(
    query: str,
    collection: str = "wallet_profiles",
    limit: int = 10,
    min_similarity: float = 0.0,
) -> list[dict[str, Any]]:
    """DEPRECATED — semantic search across one collection (old API shape)."""
    from rugmunchbot._vendor.rag_pkg.retrieval import HybridRetriever

    try:
        hits = await HybridRetriever().retrieve(
            query=query,
            collection=collection,
            top_k=limit,
            min_similarity=min_similarity,
            expand=False,
        )
        return [_hit_to_dict(h, collection) for h in hits]
    except Exception as e:  # fail-open, like the old service
        logger.debug("search_similar_failed collection=%s err=%s", collection, str(e)[:120])
        return []


async def search_multi_collection(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
) -> list[dict[str, Any]]:
    """DEPRECATED — search across multiple collections, merged (old API shape)."""
    from rugmunchbot._vendor.rag_pkg.retrieval import HybridRetriever

    targets = collections or _DEFAULT_COLLECTIONS
    try:
        by_collection = await HybridRetriever().retrieve_multi(
            query=query,
            collections=targets,
            top_k=limit,
            min_similarity=min_similarity,
        )
        merged: list[dict[str, Any]] = []
        for coll, hits in by_collection.items():
            merged.extend(_hit_to_dict(h, coll) for h in hits)
        merged.sort(key=lambda r: r["similarity"], reverse=True)
        return merged[:limit]
    except Exception as e:  # fail-open
        logger.debug("search_multi_collection_failed err=%s", str(e)[:120])
        return []


async def three_pillar_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    min_similarity: float = 0.5,
    entity_boost: float = 1.5,
    use_mmr: bool = True,
    mmr_lambda: float = 0.6,
    use_kg: bool = True,
    use_parent_child: bool = True,
) -> dict[str, Any]:
    """DEPRECATED — three-pillar search with old dict return shape.

    Returns ``{"results": [...], "confidence": ...}`` exactly like the old
    ``app.rag_service.three_pillar_search`` so callers keep working.

    Cross-encoder rerank (BAAI/bge-reranker-v2-m3) is MANDATORY: the engine
    facade reranks every result list and applies the context-token budget
    internally, so the legacy ``use_reranker`` switch is gone.
    """
    from rugmunchbot._vendor.rag_pkg.engine import three_pillar_search as _engine_search

    targets = collections or _DEFAULT_COLLECTIONS
    results: list[dict[str, Any]] = []
    for coll in targets:
        try:
            raw = await _engine_search(
                query=query,
                collection=coll,
                top_k=limit,
                min_similarity=min_similarity,
            )
            results.extend(_hit_to_dict(h, coll) for h in raw)
        except Exception as e:
            logger.debug("three_pillar_collection_failed coll=%s err=%s", coll, str(e)[:100])
    results.sort(key=lambda r: r["similarity"], reverse=True)
    results = results[:limit]
    confidence = _estimate_confidence(results)
    return {"results": results, "confidence": confidence}


def _estimate_confidence(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Best-effort confidence block matching the old service's shape."""
    if not results:
        return None
    top = results[0].get("similarity", 0.0)
    return {
        "score": round(top, 4),
        "label": "high" if top >= 0.7 else "medium" if top >= 0.4 else "low",
        "result_count": len(results),
    }


# ── Scam detection shim ────────────────────────────────────────────────────


async def detect_scam_patterns(token_data: dict[str, Any], threshold: float = 0.65) -> dict[str, Any]:
    """DEPRECATED — compare token against known scam patterns (old API shape).

    Backed by the new engine's ``RAGIntelligenceEnricher`` plus the
    ``app.crypto_embeddings`` known-pattern corpus. Returns the old dict
    shape: ``quick_matches`` / ``deep_matches`` / ``highest_threat`` /
    ``threat_severity``.
    """
    quick_matches: list[dict[str, Any]] = []
    deep_matches: list[dict[str, Any]] = []

    # 1. Quick code-snippet keyword matches (same corpus as old service)
    try:
        from app.crypto_embeddings import KNOWN_SCAM_PATTERNS

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

    # 2. Deep semantic matches via the new engine
    try:
        from rugmunchbot._vendor.rag_pkg.intelligence import RAGIntelligenceEnricher

        enricher = RAGIntelligenceEnricher(top_k=5)
        hits = await enricher.find_similar_scams(
            token_address=token_data.get("address", ""),
            chain=token_data.get("chain", ""),
            symbol=token_data.get("symbol", ""),
            name=token_data.get("name", ""),
        )
        for h in hits:
            score = float(getattr(h, "final_score", getattr(h, "score", 0.0)) or 0.0)
            if score >= threshold:
                deep_matches.append(
                    {
                        "pattern": (h.metadata or {}).get("pattern_name", h.metadata.get("name", "")),
                        "severity": (h.metadata or {}).get("severity", "high"),
                        "description": h.content[:300],
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


# ── Ingestion shims ────────────────────────────────────────────────────────


async def ingest_document(
    collection: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    doc_id: str | None = None,
) -> dict[str, Any]:
    """DEPRECATED — ingest one document (old positional API)."""
    from rugmunchbot._vendor.rag_pkg.engine import ingest_document as _engine_ingest

    if doc_id is None:
        import hashlib
        from datetime import UTC, datetime

        doc_id = hashlib.sha256(f"{collection}:{content[:100]}:{datetime.now(UTC).isoformat()}".encode()).hexdigest()[
            :16
        ]
    try:
        return await _engine_ingest(
            collection=collection,
            doc_id=doc_id,
            content=content,
            metadata=metadata or None,
        )
    except Exception as e:
        logger.debug("ingest_document_failed coll=%s err=%s", collection, str(e)[:120])
        return {"status": "error", "error": str(e)}


async def index_document(
    collection: str,
    doc_id: str,
    text: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """DEPRECATED — index a document (was a no-op in rag_service)."""
    return await ingest_document(collection=collection, content=text, metadata=metadata, doc_id=doc_id)


async def search_documents(
    collection: str,
    text: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """DEPRECATED — search a single collection for docs (was a no-op in rag_service)."""
    from rugmunchbot._vendor.rag_pkg.retrieval import HybridRetriever

    try:
        hits = await HybridRetriever().retrieve(
            query=text,
            collection=collection,
            top_k=limit,
            expand=False,
        )
        return [_hit_to_dict(h, collection) for h in hits]
    except Exception as e:
        logger.debug("search_documents_failed coll=%s err=%s", collection, str(e)[:120])
        return []


__all__ = [
    "_get_redis",
    "detect_scam_patterns",
    "get_embedder",
    "index_document",
    "ingest_document",
    "search_documents",
    "search_multi_collection",
    "search_similar",
    "three_pillar_search",
]
