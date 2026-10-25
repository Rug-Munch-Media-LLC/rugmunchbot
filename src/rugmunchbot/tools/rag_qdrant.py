"""Direct Qdrant grounding lane — bot = HOST -> 127.0.0.1:6334.

The rag-api HTTP wrapper is slow (~9s, RRF + query-transform) and the backend
container cannot reach it. Direct Qdrant vector search is ~0.03s per
collection; the whole lane is bound by ONE query embedding (~1-3s), which is
what makes it the primary grounding lane. (2026-08-30)

Embedding (2026-10-17): the corpus is Qwen3-Embedding-8B / 4096-dim — the
4B/2560 lane is RETIRED — and the local llama.cpp embedder (:8004) is not
running. Embedding is delegated to RugBrain's ``/api/v1/embed``, which owns
the DeepInfra -> SiliconFlow -> OpenRouter chain. A direct provider endpoint
(OpenAI ``{"input": ...}`` shape) still works, so the env override remains a
supported path.

Visibility (2026-10-25): every failure returns a TYPED outcome (``rag_outcomes``)
with a reason and emits a structured ``warning``. A vector whose width is not
the corpus width is REJECTED — never truncated or padded: a wrong-width vector
either 400s in Qdrant or silently scores garbage against the 4096-d index.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.rag_outcomes import EmbedOutcome, LaneOutcome, expected_dims
from rugmunchbot.tools.rag_qdrant_search import (
    collection_search_outcome,
)

logger = get_logger("rmi_bot.tools.rag_qdrant")

#: Overall budget for the lane (seconds) — one embed + N collection searches.
RAG_LOOKUP_TIMEOUT = 5.0

_DEFAULT_QDRANT_URL = "http://127.0.0.1:6334"
_DEFAULT_EMBED_URL = "http://127.0.0.1:8012/api/v1/embed"
_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding-8b")


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


def _coerce(vector: list[Any]) -> EmbedOutcome:
    """Float-coerce and width-check a raw vector; REJECT a wrong-width one."""
    try:
        floats = [float(x) for x in vector]
    except (TypeError, ValueError) as exc:
        logger.warning("rag_qdrant:embed_non_numeric err=%s", type(exc).__name__)
        return EmbedOutcome(reason=f"embed_non_numeric:{type(exc).__name__}")
    want = expected_dims()
    if len(floats) != want:
        # Never truncate/pad: a silent width change is a corrupt-index bug.
        logger.warning(
            "rag_qdrant:embed_dim_mismatch expected=%d got=%d action=reject", want, len(floats)
        )
        return EmbedOutcome(reason=f"dim_mismatch:{len(floats)}!={want}", dims=len(floats))
    return EmbedOutcome(vector=floats, ok=True, dims=len(floats))


async def embed_query_outcome(query: str) -> EmbedOutcome:
    """Embed ``query`` on the 8B/4096 lane, returning a typed outcome."""
    if not (query or "").strip():
        return EmbedOutcome(reason="empty_query")
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
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError) as exc:
        logger.warning("rag_qdrant:embed_unavailable url=%s err=%s", url, str(exc)[:160])
        return EmbedOutcome(reason=f"embed_unavailable:{type(exc).__name__}")
    if resp.status_code != 200:
        logger.warning("rag_qdrant:embed_http url=%s status=%s", url, resp.status_code)
        return EmbedOutcome(reason=f"embed_http_{resp.status_code}")
    try:
        raw = _extract_vector(resp.json())
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("rag_qdrant:embed_bad_body err=%s", type(exc).__name__)
        return EmbedOutcome(reason=f"embed_bad_body:{type(exc).__name__}")
    if not raw:
        logger.warning("rag_qdrant:embed_empty url=%s", url)
        return EmbedOutcome(reason="embed_empty")
    return _coerce(raw)


async def embed_query(query: str) -> list[float] | None:
    """Embed ``query``; ``None`` on any failure (never raises)."""
    return (await embed_query_outcome(query)).vector


def _aggregate(results: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Fold per-collection outcomes into ``(hits, failure reasons)``."""
    hits: list[dict[str, Any]] = []
    failures: list[str] = []
    for res in results:
        if isinstance(res, LaneOutcome):
            hits.extend(res.hits)
            if not res.ok:
                failures.append(res.reason or "collection_failed")
        elif isinstance(res, BaseException):
            failures.append(type(res).__name__)
    return hits, failures


def _lane_result(hits: list[dict[str, Any]], failures: list[str], total: int) -> LaneOutcome:
    """Log coverage and build the typed lane outcome."""
    if failures:
        logger.warning(
            "rag_qdrant:lane_partial failed=%d/%d hits=%d first=%s",
            len(failures),
            total,
            len(hits),
            failures[0],
        )
    elif not hits:
        logger.info("rag_qdrant:lane_empty collections=%d (no corpus hits)", total)
    return LaneOutcome(
        hits=hits, ok=bool(hits), degraded=bool(failures), reason=";".join(failures)[:200]
    )


async def direct_qdrant_outcome(
    search_query: str, collections: list[str], limit: int
) -> LaneOutcome:
    """Embed ONCE, search every collection, and report coverage as typed state."""
    embed = await embed_query_outcome(search_query)
    if not embed.ok or not embed.vector:
        logger.warning(
            "rag_qdrant:lane_degraded stage=embed reason=%s collections=%d",
            embed.reason or "no_vector",
            len(collections),
        )
        return LaneOutcome(ok=False, degraded=True, reason=embed.reason or "no_vector")
    tasks = [collection_search_outcome(c, embed.vector, limit) for c in collections]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=RAG_LOOKUP_TIMEOUT
        )
    except TimeoutError:
        logger.warning("rag_qdrant:lane_degraded stage=search reason=timeout")
        return LaneOutcome(ok=False, degraded=True, reason="search_timeout")
    hits, failures = _aggregate(list(results))
    return _lane_result(hits, failures, len(results))


async def direct_qdrant(
    search_query: str, collections: list[str], limit: int
) -> list[dict[str, Any]]:
    """Embed once + search all collections; ``[]`` on failure (never raises)."""
    return (await direct_qdrant_outcome(search_query, collections, limit)).hits
