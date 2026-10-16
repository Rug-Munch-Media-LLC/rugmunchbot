"""upsert() must send the vector shape the collection actually has.

Regression: qdrant_store.upsert always sent {"dense": ...} while rag_documents
is an UNNAMED 4096-dim collection -> 400 "Not existing vector name" -> every
scam-store write failed silently.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot._vendor.rag_pkg import qdrant_store as qs


class _FakeClient:
    def __init__(self, named: bool) -> None:
        self.named = named
        self.upserts: list[dict[str, Any]] = []

    async def get_collection(self, name: str):
        params = type("P", (), {})()
        if self.named:
            params.vectors = {"dense": object()}
        else:
            params.vectors = type("V", (), {})()
        params.sparse_vectors = {"sparse": object()} if self.named else None
        cfg = type("C", (), {})()
        cfg.config = type("Config", (), {"params": params})()
        return cfg

    async def upsert(self, **kwargs: Any):
        self.upserts.append(kwargs)


@pytest.mark.asyncio
async def test_unnamed_collection_gets_a_plain_vector():
    store = qs.QdrantStore.__new__(qs.QdrantStore)
    store.collection_name = "rag_documents"
    store._client = _FakeClient(named=False)
    store._config_probed = False
    store._has_named_dense = False
    store._has_sparse = False

    async def _ok() -> bool:
        return True

    store.ensure_collection = _ok
    store.point_id = lambda d: "pid"
    assert await store.upsert("doc", [0.1] * 8, sparse_vector=None, payload={}) is True
    vec = store._client.upserts[0]["points"][0].vector
    assert isinstance(vec, list), "unnamed collections must receive a plain vector list"


@pytest.mark.asyncio
async def test_named_collection_still_gets_the_dense_name():
    store = qs.QdrantStore.__new__(qs.QdrantStore)
    store.collection_name = "hybrid"
    store._client = _FakeClient(named=True)
    store._config_probed = False
    store._has_named_dense = False
    store._has_sparse = False

    async def _ok() -> bool:
        return True

    store.ensure_collection = _ok
    store.point_id = lambda d: "pid"
    assert await store.upsert("doc", [0.1] * 8, sparse_vector=None, payload={}) is True
    vec = store._client.upserts[0]["points"][0].vector
    assert isinstance(vec, dict)
    assert qs.DENSE_VECTOR_NAME in vec


@pytest.mark.asyncio
async def test_empty_embedding_is_skipped_not_upserted(monkeypatch):
    """An empty dense vector must be skipped, never sent to Qdrant.

    Regression: the embedder can return [] on a transient upstream fault;
    upserting it produced HTTP 422 "dense vector must not be empty" on every
    doc and spammed the log. The guard must fail-open (return False) without
    calling store.upsert.
    """
    store = qs.QdrantStore.__new__(qs.QdrantStore)
    store.collection_name = "rag_documents"
    store._client = _FakeClient(named=False)
    store._config_probed = False
    store._has_named_dense = False
    store._has_sparse = False

    async def _ok() -> bool:
        return True

    store.ensure_collection = _ok
    store.point_id = lambda d: "pid"

    async def _empty(_text: str) -> list[float]:
        return []

    monkeypatch.setattr(qs, "get_embedding", _empty)
    monkeypatch.setattr(qs, "get_qdrant_store", lambda _c=None: store)

    assert await qs.upsert_rag_document("doc", "some text") is False
    assert store._client.upserts == [], "empty vector must never reach Qdrant"
