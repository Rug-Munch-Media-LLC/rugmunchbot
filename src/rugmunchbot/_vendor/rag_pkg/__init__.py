# VENDORED 2026-08-17 from rmi-backend/app/rag/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG domain - v3 M3 engine surface.

Public API (per v3 unfuck guide §M3):
- RAGService, init_rag
- SearchRequest, SearchResponse, SearchHit
- IngestRequest, IngestResult
- FeedbackRecord
- EmbeddingProvider, COLLECTIONS
- config: RAG_INGESTION_BATCH_SIZE, RAG_CHUNK_SIZE, RAG_COLLECTIONS, ...
- ingestion: DocumentProcessor, QualityFilter, RateLimiter, BatchIngestor,
  IngestionScheduler, get_ingestor, ingest_scan_result, ...
- retrieval: HybridRetriever, ContextBuilder, RelevanceScorer, QueryExpander
"""

from __future__ import annotations

from rugmunchbot._vendor import health as health_mod
from rugmunchbot._vendor.health import DomainHealth


async def _health_check() -> DomainHealth:
    """RAG health: v3 engine present + Redis reachable."""
    try:
        from rugmunchbot._vendor.rag_pkg.engine import get_stats

        stats = get_stats()
        return DomainHealth(
            name="rag",
            healthy=True,
            details={
                "engine": "v3-m3",
                "backend": stats.get("backend", "unknown"),
                "total_docs": stats.get("total_docs", 0),
            },
        )
    except Exception as e:
        return DomainHealth(name="rag", healthy=False, error=str(e))


health_mod.register_health_check("rag", _health_check)

# Public API
# ── New pipeline exports ─────────────────────────────────────────────
# Config
from rugmunchbot._vendor.rag_pkg.config import (  # noqa: E402 — module docstring precedes import block
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RAG_COLLECTIONS as PIPELINE_COLLECTIONS,
    RAG_INGESTION_BATCH_SIZE,
    RAG_INGESTION_MAX_PER_MINUTE,
    RAG_MIN_CHUNK_CHARS,
    RAG_MIN_DOC_LENGTH,
    RAG_SCHEDULER_INTERVAL,
    RAG_SPAM_REPETITION_THRESHOLD,
)

# Ingestion pipeline
from rugmunchbot._vendor.rag_pkg.ingestion import (  # noqa: E402 — module docstring precedes import block
    BatchIngestor,
    DocumentProcessor,
    IngestionScheduler,
    IngestionStatus,
    IngestReport,
    QualityFilter,
    RateLimiter,
    get_ingestor,
    get_processor,
    get_rate_limiter,
    get_scheduler,
    ingest_news_article,
    ingest_research_post,
    ingest_scam_pattern,
    ingest_scan_result,
    ingest_wallet_profile,
)
from rugmunchbot._vendor.rag_pkg.models import (  # noqa: E402 — module docstring precedes import block
    COLLECTIONS,
    EmbeddingProvider,
    FeedbackRecord,
    IngestRequest,
    IngestResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

# Retrieval
from rugmunchbot._vendor.rag_pkg.retrieval import (  # noqa: E402 — module docstring precedes import block
    ContextBuilder,
    ContextWindow,
    HybridRetriever,
    QueryExpander,
    RelevanceScorer,
    ScoredHit,
    build_context,
    expand_query,
    search as hybrid_search,
)
from rugmunchbot._vendor.rag_pkg.service import (  # noqa: E402 — module docstring precedes import block
    RAGService,
    init_rag,
)

# Story fetch + ingest glue (bot story_fetch chain + RAG ingest_url)
from rugmunchbot._vendor.rag_pkg.story_ingest import (  # noqa: E402 — module docstring precedes import block
    StoryFetch,
    StoryFetchError,
    fetch_story,
    ingest_url,
)

__all__ = [
    "COLLECTIONS",
    # Config
    "PIPELINE_COLLECTIONS",
    "RAG_CHUNK_OVERLAP",
    "RAG_CHUNK_SIZE",
    "RAG_INGESTION_BATCH_SIZE",
    "RAG_INGESTION_MAX_PER_MINUTE",
    "RAG_MIN_CHUNK_CHARS",
    "RAG_MIN_DOC_LENGTH",
    "RAG_SCHEDULER_INTERVAL",
    "RAG_SPAM_REPETITION_THRESHOLD",
    # Ingestion
    "BatchIngestor",
    # Retrieval
    "ContextBuilder",
    "ContextWindow",
    "DocumentProcessor",
    "EmbeddingProvider",
    "FeedbackRecord",
    "HybridRetriever",
    "IngestReport",
    "IngestRequest",
    "IngestResult",
    "IngestionScheduler",
    "IngestionStatus",
    "QualityFilter",
    "QueryExpander",
    "RAGService",
    "RateLimiter",
    "RelevanceScorer",
    "ScoredHit",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "StoryFetch",
    "StoryFetchError",
    "build_context",
    "expand_query",
    "fetch_story",
    "get_ingestor",
    "get_processor",
    "get_rate_limiter",
    "get_scheduler",
    "hybrid_search",
    "ingest_news_article",
    "ingest_research_post",
    "ingest_scam_pattern",
    "ingest_scan_result",
    "ingest_url",
    "ingest_wallet_profile",
    "init_rag",
]
