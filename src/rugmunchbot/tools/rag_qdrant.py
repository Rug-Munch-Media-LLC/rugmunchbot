"""Direct Qdrant grounding lane — bot = HOST -> 127.0.0.1:6334.

The rag-api HTTP wrapper is slow (~9s, RRF + query-transform) and the backend
container cannot reach it. Direct Qdrant vector search is ~0.03s per
collection; the whole lane is bound by ONE query embedding (~1-3s), which is
what makes it the primary grounding lane. (2026-08-30)

Embedding (2026-10-17): the corpus is Qwen3-Embedding-8B / 4096-dim — the
4B/2560 lane is RETIRED — and the local llama.cpp embedder (:8004) is not
running. Embedding is therefore delegated to RugBrain's ``/api/v1/embed``,
which owns the DeepInfra -> SiliconFlow -> OpenRouter fallback chain and keeps
this lane alive when any single provider is down. A direct provider endpoint
(the OpenAI ``{"input": ...}`` shape) still works, so the env override remains
a supported path.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.rag_qdrant")

#: Overall budget for the lane (seconds) — one embed + N collection searches.
RAG_LOOKUP_TIMEOUT = 5.0

_DEFAULT_QDRANT_URL = "http://127.0.0.1:6334"
_DEFAULT_EMBED_URL = "http://127.0.0.1:8012/api/v1/embed"
_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")
_EMBED_DIMS_EXPECTED = int(os.getenv("RAG_EMBED_DIMS", "4096"))


def qdrant_url() -> str:
    """Qdrant REST base (env ``QDRANT_URL``, host loopback default)."""
    return (os.getenv("QDRANT_URL") or _DEFAULT_QDRANT_URL).rstrip("/")


def rag_headers() -> dict[str, str]:
    """Auth headers for RugBrain's own endpoints (the ``/api/v1/embed`` lane)."""
    from rugmunchbot.tools.rag_lookup import _rag_api_key

    key = _rag_api_key()
    return {"X-API-Key": key} if key else {}


def embed_url() -> str:
    """Embed endpoint — RugBrain ``/api/v1/embed`` unless overridden."""
    return (os.getenv("QWEN_EMBED_URL") or os.getenv("RMI_EMBED_URL") or _DEFAULT_EMBED_URL).rstrip(
        "/"
    )


def _embed_payload(url: str, query: str) -> dict[str, Any]:
    """``/api/v1/embed`` takes ``{"text": ...}``; providers take OpenAI's shape."""
    if url.endswith("/api/v1/embed"):
        return {"text": query}
    return {"model": _EMBED_MODEL, "input": query}


def _extract_vector(raw: dict[str, Any]) -> list[Any] | None:
    """``/api/v1/embed`` -> ``{"embedding": [...]}``; OpenAI -> ``{"data":[...]}``."""
    vec = raw.get("embedding")
    if vec is None:
        vec = raw["data"][0]["embedding"]
    return vec if isinstance(vec, list) and vec else None


async def embed_query(query: str) -> list[float] | None:
    """Embed ``query`` on the 8B/4096 lane.

    Returns the vector or ``None`` on any failure (never raises). One embedding
    serves every collection in the lane — 4096d cosine is collection-agnostic
    inside the 8B corpus.
    """
    url = embed_url()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0, read=8.0, write=8.0)
        ) as client:
            resp = await asyncio.wait_for(
                client.post(
                    url,
                    headers={**rag_headers(), "Content-Type": "application/json"},
                    json=_embed_payload(url, query),
                ),
                timeout=8.0,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
        return None
    if resp.status_code != 200:
        return None
    try:
        vec = _extract_vector(resp.json())
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return [float(x) for x in vec] if vec else None


def _first(payload: dict[str, Any], *keys: str) -> str:
    """First non-empty trimmed value among *keys* (branch-flat helper)."""
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _hit(point: dict[str, Any], collection: str) -> dict[str, Any] | None:
    """Map one Qdrant point to the fleet hit shape (None when empty)."""
    pl = point.get("payload") or {}
    content = _first(pl, "content", "text")
    if not content:
        return None
    meta = pl.get("metadata")
    return {
        "content": content,
        "score": float(point.get("score") or 0.0),
        "collection": pl.get("collection") or collection,
        "metadata": meta if isinstance(meta, dict) else {},
        "id": _first(pl, "doc_id") or str(point.get("id") or ""),
    }


async def collection_search(
    collection: str, vector: list[float], limit: int
) -> list[dict[str, Any]]:
    """Vector-search one Qdrant collection (``/collections/{c}/points/search``).

    Maps each hit to the standard ``{content, score, collection, metadata}``
    shape. Returns ``[]`` on any failure — never raises.
    """
    url = f"{qdrant_url()}/collections/{collection}/points/search"
    payload = {"vector": vector, "limit": limit, "with_payload": True}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0, read=6.0, write=6.0)
        ) as client:
            resp = await asyncio.wait_for(
                client.post(url, headers={"Content-Type": "application/json"}, json=payload),
                timeout=6.0,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
        return []
    if resp.status_code != 200:
        return []
    try:
        raw = resp.json()
    except ValueError:
        return []
    result = raw.get("result") if isinstance(raw, dict) else None
    points = result if isinstance(result, list) else []
    return [h for h in (_hit(p, collection) for p in points if isinstance(p, dict)) if h]


async def direct_qdrant(
    search_query: str, collections: list[str], limit: int
) -> list[dict[str, Any]]:
    """Embed ONCE and vector-search every collection in the knowledge set.

    Bound to ~8s total (embed ~1-3s + per-collection @0.03s). Runs the
    collection searches concurrently. Returns collected hits (not yet deduped)
    or ``[]`` on failure — never raises.
    """
    vector = await embed_query(search_query)
    if not vector:
        return []
    tasks = [collection_search(c, vector, limit) for c in collections]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=RAG_LOOKUP_TIMEOUT
        )
    except TimeoutError:
        return []
    out: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, list):
            out.extend(res)
    return out
