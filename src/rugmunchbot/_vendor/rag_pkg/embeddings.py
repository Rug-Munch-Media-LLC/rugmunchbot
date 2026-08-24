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


async def get_embedding(text: str, model: str = "") -> list[float]:
    """Get embedding vector via rugbrain POST /api/v1/embed (Qwen3-4B)."""
    import os

    import httpx

    api_url = os.getenv("RAG_API_URL", "http://rugbrain-api:8012/api/v1")
    api_key = os.getenv("RMI_RAG_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{api_url}/embed",
                json={"text": text[:512]},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json().get("embedding", [])
    except Exception as exc:
        logger.debug("embedding fetch failed, returning empty: %s", exc)
    return []
