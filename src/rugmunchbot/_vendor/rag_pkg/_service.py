# VENDORED 2026-08-17 from rmi-backend/app/rag/_service.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""TA knowledge ingestion/serving helpers.

Extracted from ``app/rag/ta_knowledge.py`` during the de-godding pass.
The knowledge content lives in ``app/rag._knowledge`` (``TA_KNOWLEDGE_DOCS``);
this module holds the convenience ingestion + summary helpers that operate on
it. ``ta_knowledge.py`` re-exports every public name.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.rag_pkg._knowledge import TA_KNOWLEDGE_DOCS

# ── Convenience ingestion function ──────────────────────────────────────


async def ingest_ta_knowledge() -> dict[str, Any]:
    """Ingest all TA methodology patterns into the RAG vector store.

    This is a convenience function that can be called during startup or
    via an admin endpoint to populate the "ta_methodology" collection.

    Returns:
        Dict with status and count of ingested documents.
    """
    from rugmunchbot._vendor.rag_pkg.engine import bulk_ingest

    try:
        result = await bulk_ingest(items=TA_KNOWLEDGE_DOCS, collection="ta_methodology")
        return {
            "status": "ok",
            "collection": "ta_methodology",
            "documents_count": len(TA_KNOWLEDGE_DOCS),
            "result": result,
        }
    except Exception as e:
        return {
            "status": "error",
            "collection": "ta_methodology",
            "error": str(e),
        }


def get_ta_knowledge_summary() -> dict[str, Any]:
    """Get a summary of TA knowledge topics available for ingestion, without
    actually ingesting them. Useful for checking what's available."""
    categories: dict[str, int] = {}
    for doc in TA_KNOWLEDGE_DOCS:
        cat = doc.get("metadata", {}).get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    return {
        "total_documents": len(TA_KNOWLEDGE_DOCS),
        "categories": categories,
        "doc_ids": [doc["doc_id"] for doc in TA_KNOWLEDGE_DOCS],
        "collection": "ta_methodology",
    }
