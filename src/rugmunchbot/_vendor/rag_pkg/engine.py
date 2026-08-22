# VENDORED 2026-08-17 from rmi-backend/app/rag/engine.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""M3 RAG Engine - three-pillar search + ingest.

Re-export shim. During the Qdrant-consolidation de-godding pass the engine
bodies moved to focused submodules:

  * ``_qdrant`` — Qdrant storage/client primitives (collection probing,
    named-vector detection, legacy HTTP vector-search fallback, collection
    resolution, sparse query builder, additive chunk upsert).
  * ``_search`` — three-pillar retrieval (Qdrant primary + legacy pillars),
    RRF fusion, mandatory cross-encoder rerank, context-token budget.
  * ``_ingest`` — document ingestion, bulk ingest, collection stats.

This module keeps the public API intact: every name is re-exported below
so existing ``app.rag.engine`` importers keep working unchanged.

Import-time side effects are preserved: importing this shim imports the
submodules, which read the environment (``RAG_CONTEXT_MAX_TOKENS``,
``RAG_STORE``, ``RAG_QDRANT_COLLECTION``) and ``app.config.settings`` at
module load, exactly as the original ``engine.py`` did.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import redis

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg._ingest import (
    bulk_ingest,
    get_collection_stats,
    get_stats,
    ingest_document,
)
from rugmunchbot._vendor.rag_pkg._qdrant import (
    _DEFAULT_QDRANT_COLLECTION,
    _QDRANT_COLLECTIONS,
    _QDRANT_PROBED,
    _QDRANT_RESOLVED,
    _QDRANT_URL,
    _SEARCH_STOPWORDS,
    _build_sparse_query,
    _qdrant_collection_exists,
    _qdrant_search,
    _qdrant_upsert_chunk,
    _qdrant_uses_named_vectors,
    _resolve_qdrant_collection,
    _timestamp_epoch,
)
from rugmunchbot._vendor.rag_pkg._search import (
    _CONTEXT_MAX_TOKENS,
    _DEFAULT_STORE,
    _RERANK_MAX_CHARS,
    _RRF_K,
    _apply_context_budget,
    _apply_filters,
    _keyword_search,
    _legacy_pillar_search,
    _matches_filters,
    _qdrant_primary_search,
    _reciprocal_rank_fusion,
    _rerank_hits,
    three_pillar_search,
)
from rugmunchbot._vendor.rag_pkg.ann_index import Hit, get_index
from rugmunchbot._vendor.rag_pkg.chunking import (
    Chunk,
    chunk_text,
    content_hash,
    estimate_tokens,
    is_duplicate,
    mark_ingested,
)
from rugmunchbot._vendor.rag_pkg.config import RAG_CONTEXT_MAX_TOKENS
from rugmunchbot._vendor.rag_pkg.embeddings import current_backend, get_embedding
from rugmunchbot._vendor.rag_pkg.tracing import trace_span
from rugmunchbot._vendor.settings_shim import settings

log = get_logger(__name__)

# Explicit re-exports: mypy's ``no_implicit_reexport`` (strict) requires names
# imported from the split submodules and re-exported to external importers to
# be listed here. The dependency names (``Hit``, ``get_embedding``,
# ``chunk_text``, ...) were module-level in the original engine.py and are
# preserved so ``mock.patch("app.rag.engine.X")`` and lazy
# ``from rugmunchbot._vendor.rag_pkg.engine import X`` keep resolving.
__all__ = [
    "RAG_CONTEXT_MAX_TOKENS",
    "UTC",
    "_CONTEXT_MAX_TOKENS",
    "_DEFAULT_QDRANT_COLLECTION",
    "_DEFAULT_STORE",
    "_QDRANT_COLLECTIONS",
    "_QDRANT_PROBED",
    "_QDRANT_RESOLVED",
    "_QDRANT_URL",
    "_RERANK_MAX_CHARS",
    "_RRF_K",
    "_SEARCH_STOPWORDS",
    "Any",
    "Chunk",
    "Hit",
    "_apply_context_budget",
    "_apply_filters",
    "_build_sparse_query",
    "_keyword_search",
    "_legacy_pillar_search",
    "_matches_filters",
    "_qdrant_collection_exists",
    "_qdrant_primary_search",
    "_qdrant_search",
    "_qdrant_upsert_chunk",
    "_qdrant_uses_named_vectors",
    "_reciprocal_rank_fusion",
    "_rerank_hits",
    "_resolve_qdrant_collection",
    "_timestamp_epoch",
    "bulk_ingest",
    "chunk_text",
    "content_hash",
    "current_backend",
    "datetime",
    "estimate_tokens",
    "get_collection_stats",
    "get_embedding",
    "get_index",
    "get_logger",
    "get_stats",
    "httpx",
    "ingest_document",
    "is_duplicate",
    "log",
    "mark_ingested",
    "os",
    "redis",
    "settings",
    "three_pillar_search",
    "time",
    "trace_span",
]
