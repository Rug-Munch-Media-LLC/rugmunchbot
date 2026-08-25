# VENDORED 2026-08-24 from rmi-backend/app/rag/embeddings.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Embedding utilities — delegates to rugbrain HTTP API.

Primary embedder: Qwen3-Embedding-4B (2560d, self-hosted llama-server).
Dimension MUST match rugbrain's EMBEDDING_DIM and the Qdrant collection.
"""

import logging
from typing import Final

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME: Final = "qwen3-embedding-4b"
EMBEDDING_DIM: Final = 2560


class RAGUnavailableError(Exception):
    """RAG system is unavailable."""


def current_backend() -> str:
    """Which embedding backend is active? For observability metadata.

    Deviation note (2026-08-25): callers (_ingest, _search, engine) import
    this, but the rmi-backend upstream this file is vendored from does not
    define it yet — refresh-vendors.sh cannot restore it. This bot embeds
    exclusively via the rugbrain HTTP API (never locally), so the honest
    answer is the operator override or 'remote'.
    """
    import os

    return os.getenv("RAG_EMBED_BACKEND") or "remote"


async def get_embedding(text: str, model: str = "", dimensions: int | None = None) -> list[float]:
    """Get embedding vector via rugbrain POST /api/v1/embed (Qwen3-4B).

    ``dimensions`` pins the output to the TARGET COLLECTION's dim (MRL
    truncate-down server-side). Ingest callers MUST pass the collection's
    dim — post-migration collections are native 2560 while this endpoint
    historically returned 1024, which silently killed every upsert.
    """
    import os

    import httpx

    api_url = os.getenv("RAG_API_URL", "http://rugbrain-api:8012/api/v1")
    api_key = os.getenv("RMI_RAG_API_KEY", "")
    payload: dict = {"text": text[:512]}
    dims = dimensions or os.getenv("RAG_EMBED_DIMS")
    if dims:
        payload["dimensions"] = int(dims)
    try:
        async with httpx.AsyncClient(timeout=float(os.getenv("RAG_EMBED_TIMEOUT", "45"))) as client:
            r = await client.post(
                f"{api_url}/embed",
                json=payload,
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json().get("embedding", [])
    except Exception as exc:
        logger.debug("embedding fetch failed, returning empty: %s", exc)
    return []
