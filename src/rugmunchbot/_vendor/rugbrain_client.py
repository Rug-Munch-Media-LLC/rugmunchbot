# VENDORED 2026-08-24 from rmi-backend/app/integrations/rugbrain_client.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
RugBrain HTTP client — replaces direct ``from app.rag import ...`` calls.

Usage::

    from rugmunchbot._vendor.rugbrain_client import rugbrain_search, rugbrain_ingest

    results = await rugbrain_search("flash loan", collections=["defi_exploits"])
    ok = await rugbrain_ingest(content="...", collection="defi_exploits")
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_RUGBRAIN_API_URL: str = os.getenv("RAG_API_URL", "http://rugbrain-api:8012/api/v1").rstrip("/")
_RUGBRAIN_API_KEY: str = os.getenv("RMI_RAG_API_KEY", "")
_HTTP_TIMEOUT: int = int(os.getenv("RAG_HTTP_TIMEOUT", "300"))


async def rugbrain_search(
    query: str,
    collections: list[str] | None = None,
    limit: int = 10,
    rerank: bool = False,
) -> list[dict[str, Any]]:
    """Multi-collection search via rugbrain's POST /api/v1/search/multi.

    Returns results fused via RRF across collections, sorted by relevance.
    """
    if not query:
        return []

    payload: dict[str, Any] = {
        "query": query,
        "collections": collections or ["threat_intel_v2"],
        "limit": limit,
        "rerank": rerank,
    }

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(
                f"{_RUGBRAIN_API_URL}/search/multi",
                json=payload,
                headers={"X-API-Key": _RUGBRAIN_API_KEY},
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("results", [])
            else:
                logger.warning(
                    "rugbrain_search_failed status=%s body=%s",
                    r.status_code,
                    r.text[:200],
                )
                return []
    except Exception as e:
        logger.warning("rugbrain_search_error: %s", e)
        return []


async def rugbrain_ingest(
    content: str,
    collection: str = "default",
    doc_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    source: str = "api",
    severity: str = "medium",
    chain: str = "",
) -> bool:
    """Ingest a document via rugbrain's POST /api/v1/ingest.

    Returns True if the document was ingested successfully.
    """
    payload: dict[str, Any] = {
        "documents": [
            {
                "doc_id": doc_id or content[:64],
                "content": content,
                "collection": collection,
                "metadata": metadata or {},
                "source": source,
                "severity": severity,
                "chain": chain,
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(
                f"{_RUGBRAIN_API_URL}/ingest",
                json=payload,
                headers={"X-API-Key": _RUGBRAIN_API_KEY},
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("ingested", 0) > 0
            else:
                logger.warning(
                    "rugbrain_ingest_failed status=%s body=%s",
                    r.status_code,
                    r.text[:200],
                )
                return False
    except Exception as e:
        logger.warning("rugbrain_ingest_error: %s", e)
        return False
