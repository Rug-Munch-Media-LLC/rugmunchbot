"""Reranker adapter — OSS fail-open no-op.

The live bot reranks news vs. focus queries via a private Infinity endpoint
(``app.rag.rerank.rerank_results``). OSS returns ``[]``; callers fall back to
their heuristic score map.
"""

from __future__ import annotations

from typing import Any


async def rerank_results(
    query: str, texts: list[str], top_n: int | None = None
) -> list[dict[str, Any]]:
    """No-op: no reranker in OSS, return empty result list."""
    return []


__all__ = ["rerank_results"]
