# VENDORED 2026-08-23 from rmi-backend/app/rag/service.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG service — HTTP facade over rugbrain API.

Delegates all search, ingest, and feedback operations to rugbrain's
HTTP API (``app.integrations.rugbrain_client``).
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rugbrain_client import rugbrain_ingest, rugbrain_search
from rugmunchbot._vendor.rag_pkg.models import IngestRequest, IngestResult, SearchRequest, SearchResponse

logger = get_logger(__name__)


class RAGService:
    """RAG service — HTTP facade over rugbrain API."""

    async def search(self, req: SearchRequest) -> SearchResponse:
        """Search RAG via rugbrain HTTP API."""
        t0 = time.monotonic()
        results = await rugbrain_search(
            query=req.query,
            collections=[req.collection],
            limit=req.top_k,
        )
        from rugmunchbot._vendor.rag_pkg.models import SearchHit

        hits = [
            SearchHit(
                content=(r.get("content") or r.get("text") or ""),
                score=r.get("rrf_score", 0),
                metadata=r.get("metadata", {}),
                collection=req.collection,
                doc_id=r.get("doc_id", ""),
            )
            for r in results
        ]
        took_ms = (time.monotonic() - t0) * 1000
        return SearchResponse(
            query=req.query,
            hits=hits,
            took_ms=took_ms,
            collections_searched=[req.collection],
        )

    async def ingest(self, req: IngestRequest) -> IngestResult:
        """Ingest a document via rugbrain HTTP API."""
        ok = await rugbrain_ingest(
            content=req.content,
            collection=req.collection,
            doc_id=req.doc_id,
            metadata=req.metadata,
        )
        return IngestResult(
            doc_id=req.doc_id or "",
            collection=req.collection,
            status="ok" if ok else "failed",
        )

    async def record_feedback(self, record: Any) -> bool:
        """Record scanner feedback — ingest as known scam via rugbrain."""
        content = getattr(record, "content", getattr(record, "token_address", ""))
        return await rugbrain_ingest(
            content=str(content),
            collection="known_scams",
            metadata={
                "token_address": getattr(record, "token_address", ""),
                "chain": getattr(record, "chain", ""),
            },
        )


async def init_rag() -> None:
    """Initialize RAG service (no-op — rugbrain handles its own init)."""
    logger.info("RAG service initialized (rugbrain HTTP backend)")
