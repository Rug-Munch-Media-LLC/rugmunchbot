# VENDORED 2026-08-23 from rmi-backend/app/rag/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG domain — all operations now route through rugbrain HTTP API.

This module is kept for backward import compatibility. All actual
RAG functionality lives in rugbrain (rmi-rag repo). Thin wrappers
in ``app.rag.compat``, ``app.rag.intelligence``, ``app.rag.service``,
etc. route through ``app.integrations.rugbrain_client``.
"""

from __future__ import annotations

from rugmunchbot._vendor import health as health_mod
from rugmunchbot._vendor.health import DomainHealth


async def _health_check() -> DomainHealth:
    """RAG health: rugbrain HTTP API reachable."""
    try:
        import os

        import httpx

        api_key = os.getenv("RMI_RAG_API_KEY", "")
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                "http://rugbrain-api:8012/api/v1/health",
                headers={"X-API-Key": api_key} if api_key else {},
            )
            healthy = r.status_code == 200
        return DomainHealth(
            name="rag",
            healthy=healthy,
            details={"backend": "rugbrain-http", "status_code": r.status_code},
        )
    except Exception as e:
        return DomainHealth(name="rag", healthy=False, error=str(e))


health_mod.register_health_check("rag", _health_check)

# ── Backward-compatible exports ──────────────────────────────────────────
# These are re-exported for callers that do ``from app.rag import X``.
# The actual implementations are in the submodules below.

from rugmunchbot._vendor.rag_pkg.models import (  # noqa: E402
    COLLECTIONS,
    EmbeddingProvider,
    FeedbackRecord,
    IngestRequest,
    IngestResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from rugmunchbot._vendor.rag_pkg.service import RAGService, init_rag  # noqa: E402

__all__ = [
    "COLLECTIONS",
    "EmbeddingProvider",
    "FeedbackRecord",
    "IngestRequest",
    "IngestResult",
    "RAGService",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "init_rag",
]
