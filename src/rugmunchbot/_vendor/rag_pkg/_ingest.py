# VENDORED 2026-08-17 from rmi-backend/app/rag/_ingest.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Document indexing + collection stats for the M3 RAG engine.

Extracted from ``app/rag/engine.py`` during the Qdrant consolidation
de-godding pass: chunk→embed→store ingestion (with the additive Qdrant
write path), bulk ingestion and the collection/stats surface.
``engine.py`` re-exports every public name.
"""

from __future__ import annotations

import os

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import redis

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg._qdrant import _qdrant_upsert_chunk
from rugmunchbot._vendor.rag_pkg.ann_index import get_index
from rugmunchbot._vendor.rag_pkg.chunking import (
    Chunk,
    chunk_text,
    content_hash,
    estimate_tokens,
    is_duplicate,
    mark_ingested,
)
from rugmunchbot._vendor.rag_pkg.embeddings import current_backend
from rugmunchbot._vendor.rag_pkg.tracing import trace_span

log = get_logger(__name__)


#: Max concurrent chunk embed+store tasks per document. Chunk work is
#: network-bound (llama.cpp qwen3 embed + Qdrant upsert) and independent, so a
#: modest concurrency cap collapses N serial round-trips into ~N/4 without
#: hammering the embedder. Mirrors the embedder's own internal
#: ``max_connections=8`` budget.
_CHUNK_CONCURRENCY: int = 4


# ── Ingest ───────────────────────────────────────────────────────────
async def ingest_document(
    collection: str,
    doc_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    chunk: bool = True,
    content_type: str = "general",
) -> dict[str, Any]:
    """Ingest a document into the RAG system.

    - Chunks the content (content-type-aware strategies, dedup)
    - Embeds each chunk
    - Adds to the collection's ANN index
    - Marks each chunk's hash as ingested for dedup

    ``content_type`` selects the chunking strategy (solidity, whitepaper,
    audit, news, social, transaction, general). Defaults to "general".
    """
    if not content or not content.strip():
        return {"doc_id": doc_id, "collection": collection, "status": "empty", "chunks": 0}

    metadata = metadata or {}
    with trace_span(
        "rag.ingest.document",
        {
            "openinference.span.kind": "CHAIN",
            "ingest.collection": collection,
            "ingest.doc_id": doc_id,
            "ingest.content_type": content_type,
            "ingest.chunk": chunk,
        },
    ) as span:
        idx = get_index(collection)
        if chunk:
            chunks = chunk_text(content, content_type=content_type)
        else:
            # Single-chunk path: still dedup
            chunks = [
                Chunk(
                    text=content,
                    content_hash=content_hash(content),
                    index=0,
                    quality=1.0,
                    content_type=content_type,
                    source_url=metadata.get("source_url", ""),
                    timestamp=datetime.now(UTC).isoformat(),
                    token_count=estimate_tokens(content),
                )
            ]

        # Dedup
        new_chunks = [c for c in chunks if not is_duplicate(c.content_hash, collection)]
        skipped = len(chunks) - len(new_chunks)
        if not new_chunks:
            span.set_attribute("ingest.status", "duplicate")
            span.set_attribute("ingest.skipped", skipped)
            return {
                "doc_id": doc_id,
                "collection": collection,
                "status": "duplicate",
                "chunks": 0,
                "skipped": skipped,
            }

        # Embed + insert. Chunk work is independent (each chunk embeds and
        # stores on its own) and network-bound (llama.cpp qwen3 embed + Qdrant
        # upsert), so run chunks concurrently with a bounded semaphore
        # instead of serializing N chunk round-trips per document. The
        # lazy per-chunk import stays inside the worker so the
        # ``mock.patch("app.rag.embeddings.get_embedding")`` contract in
        # tests still intercepts.
        sem = asyncio.Semaphore(_CHUNK_CONCURRENCY)

        async def _embed_store(c: Chunk) -> None:
            async with sem:
                # Lazy import: call-time resolution from the embeddings module so
                # ``mock.patch("app.rag.embeddings.get_embedding")`` (and the
                # engine-shim alias) still intercept this path.
                from rugmunchbot._vendor.rag_pkg.embeddings import get_embedding

                # Pin to the target collection's dim — post-migration
                # collections are native 2560; unpinned requests returned
                # 1024 and every upsert died on the concat mismatch.
                vec = await get_embedding(
                    c.text, dimensions=int(os.getenv("RAG_INGEST_DIMS", "2560"))
                )
                chunk_doc_id = f"{doc_id}:{c.content_hash[:8]}:{c.index}"
                chunk_meta = {
                    **metadata,
                    "chunk_index": c.index,
                    "content_hash": c.content_hash,
                    "quality": c.quality,
                    "content_type": c.content_type,
                    "source_url": c.source_url,
                    "timestamp": c.timestamp,
                    "token_count": c.token_count,
                }
                idx.add(chunk_doc_id, vec, chunk_meta, text=c.text)
                mark_ingested(c.content_hash, collection)
                # Qdrant write path (consolidation Phase 1): additive + fail-open.
                # New chunks also land in the unified ``rag_documents`` collection
                # so the Qdrant primary search path stops returning empty (which
                # previously forced the legacy fallback). Legacy writes above stay.
                # The already-computed vector is passed through so the Qdrant path
                # does NOT re-embed the same chunk text (it embeds again when
                # ``vector`` is None).
                await _qdrant_upsert_chunk(chunk_doc_id, c, chunk_meta, collection, vector=vec)

        await asyncio.gather(*(_embed_store(c) for c in new_chunks))
        added = len(new_chunks)

        span.set_attribute("ingest.status", "ok")
        span.set_attribute("ingest.chunks_added", added)
        span.set_attribute("ingest.skipped", skipped)

    return {
        "doc_id": doc_id,
        "collection": collection,
        "status": "ok",
        "chunks": added,
        "skipped": skipped,
    }


# ── Stats ────────────────────────────────────────────────────────────
def get_collection_stats(collection: str) -> dict[str, Any]:
    """Get stats for one collection: vector count + dedup hashes count."""
    idx = get_index(collection)
    count = idx.count()
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        client = get_redis()
        hashes = client.scard(f"rag:hashes:{collection}") if client is not None else 0
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError):
        hashes = 0
    return {
        "collection": collection,
        "vector_count": count,
        "dedup_hashes": hashes,
    }


def get_stats(collections: list[str] | None = None) -> dict[str, Any]:
    """Get stats for all (or specified) collections + the active embedder backend."""
    from rugmunchbot._vendor.rag_pkg.models import COLLECTIONS as DEFAULT_COLLECTIONS

    cols = collections or DEFAULT_COLLECTIONS
    return {
        "total_docs": sum(get_collection_stats(c)["vector_count"] for c in cols),
        "backend": current_backend(),
        "collections": [get_collection_stats(c) for c in cols],
    }


# ── Background helpers (used by ingest_cron worker) ──────────────────
async def bulk_ingest(
    items: list[dict[str, Any]],
    collection: str = "scam_intel",
) -> dict[str, Any]:
    """Ingest many items sequentially. Returns summary counts."""
    if not items:
        return {"total": 0, "ok": 0, "duplicate": 0, "empty": 0, "errors": 0}

    counts = {"total": len(items), "ok": 0, "duplicate": 0, "empty": 0, "errors": 0}
    for it in items:
        try:
            item_meta = it.get("metadata") or {}
            r = await ingest_document(
                collection=collection,
                doc_id=it.get("doc_id")
                or it.get("id")
                or f"bulk:{content_hash(it.get('content') or it.get('text', ''))[:16]}",
                content=it.get("content") or it.get("text", ""),
                metadata=item_meta,
                content_type=(it.get("content_type") or item_meta.get("content_type") or "general"),
            )
            if r["status"] == "ok":
                counts["ok"] += 1
            elif r["status"] == "duplicate":
                counts["duplicate"] += 1
            elif r["status"] == "empty":
                counts["empty"] += 1
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("bulk_ingest_item_failed: %s", e)
            counts["errors"] += 1
    return counts


__all__ = [
    "bulk_ingest",
    "get_collection_stats",
    "get_stats",
    "ingest_document",
]
