"""Dedup / news-v2 RAG collection schema tests (2026-10-11).

Locks in the post-8B-migration collection-name contract for the news dedup
rail: the RugBrain API at 127.0.0.1:8012 accepts **BASE** collection names
(``news_v2``); the ``_8b`` suffix is resolved server-side on the hosted
rerank lane and must NEVER be sent by the client. The previous 4B / 2560-dim
lane used the ``_8b`` suffix in client calls; these tests assert the current
8B / 4096-dim lane does NOT.

If either regresses to ``news_v2_8b`` the API returns 404 for the collection
and the fallback rail silently dries up — hence a schema gate.
"""

from __future__ import annotations

from typing import Any

import pytest

import rugmunchbot._vendor.rugbrain_client as rb_client
from rugmunchbot.channels.content._rag_news import (
    _DEFAULT_COLLECTION,
    _collection,
    fetch_rag_news_items,
)

#: The single base collection name the API accepts (the ``_8b`` suffix is
#: resolved server-side on the hosted rerank lane).
_BASE_NEWS_COLLECTION = "news_v2"


@pytest.mark.asyncio
async def test_fetch_rag_news_uses_base_collection_name(monkeypatch):
    """The rugbrain-news rail must pass the BASE collection name to the API.

    The API takes BASE names; the ``_8b`` suffix is resolved server-side, so
    sending ``news_v2_8b`` would 404. Assert the exact base name AND that no
    ``_8b`` suffix ever leaves the client. Rerank is ENABLED (it IS the ranking
    on this fail-open tier).
    """
    captured: dict[str, Any] = {}

    async def _fake_search(
        query: str,
        collections: list[str] | None = None,
        limit: int = 10,
        rerank: bool = False,
    ) -> list[dict[str, Any]]:
        captured["query"] = query
        captured["collections"] = list(collections or [])
        captured["limit"] = limit
        captured["rerank"] = rerank
        return []

    monkeypatch.setattr(rb_client, "rugbrain_search", _fake_search)
    monkeypatch.delenv("RMI_RAG_NEWS_COLLECTION", raising=False)

    result = await fetch_rag_news_items(limit=5)

    assert result == []  # empty response is routed straight through
    assert captured["collections"] == [_BASE_NEWS_COLLECTION]  # exact base name
    assert not any(c.endswith("_8b") for c in captured["collections"])  # never _8b
    assert captured["rerank"] is True  # reranker IS the ranking on this rail


@pytest.mark.asyncio
async def test_collection_resolver_is_base_never_8b(monkeypatch):
    """The collection resolver must yield the BASE name and never an ``_8b``
    suffix — even under an env override the client must not append ``_8b``
    (the API resolves it server-side on the 8B lane)."""

    async def _recorder(query, collections=None, limit=10, rerank=False):
        return []

    # Default path: env unset → the base name.
    monkeypatch.delenv("RMI_RAG_NEWS_COLLECTION", raising=False)
    assert _collection() == _BASE_NEWS_COLLECTION
    assert not _collection().endswith("_8b")

    # Env override is used verbatim (BASE); the resolver never appends _8b.
    monkeypatch.setenv("RMI_RAG_NEWS_COLLECTION", "news_v2")
    assert _collection() == _BASE_NEWS_COLLECTION
    assert not _collection().endswith("_8b")
    assert "_8b" not in _collection()

    # _DEFAULT_COLLECTION matches the constant the resolver falls back to.
    assert _DEFAULT_COLLECTION == _BASE_NEWS_COLLECTION
