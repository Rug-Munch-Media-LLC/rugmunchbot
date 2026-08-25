# VENDORED 2026-08-24 from rmi-backend/app/rag/ann_index.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""M3 RAG ANN Index - numpy-based cosine similarity with Redis persistence.

Why numpy + Redis (not FAISS):
- 13 collections x ~5K docs = ~65K total - small enough for in-process numpy
- No FAISS native dep, no rebuild on container restart
- Vector + metadata stored as JSON in Redis; loaded into a numpy matrix
  on first search, then kept in process memory for fast repeated queries
- Cosine sim = dot product on L2-normalized vectors (we normalize on insert)

Persistence shape (per collection):
  rag:ann:{collection}:meta   -> JSON {count, dim}
  rag:ann:{collection}:docs   -> HASH doc_id -> {"vector": [...], "metadata": {...}, "text": "..."}

Optimisations (2026-07-24):
  - Dirty-doc tracking: _persist() only writes changed docs, not all docs
  - _id_to_idx dict for O(1) doc_id lookups (was O(N) .index() calls)
  - Batch persist: single pipe flush per add/delete instead of O(N) writes
  - Meta persisted only on first load or dimension change

Fail-open (2026-08-14):
  - Every Redis touch (``_persist``/``_load``/``delete``/``clear``) guards a
    ``None`` client from ``app.core.redis.get_redis`` (pool/auth failure
    returns None instead of raising). With Redis unavailable the in-memory
    index stays authoritative for the process, the write is logged and
    skipped, and the Qdrant primary store (``app.rag.qdrant_store``) is
    unaffected — ``ingest_url`` must never die on a bare AttributeError.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.embeddings import EMBEDDING_DIM

log = get_logger(__name__)

# Lazy import - Redis client is created on first use, after app.core.redis is ready
_redis_client = None


def _get_redis() -> Any:
    """Return the shared Redis client, or None when Redis is unavailable.

    ``app.core.redis.get_redis`` returns None (never raises) on pool/auth
    failure. None is intentionally NOT cached here, so a transient Redis
    outage can recover on a later call. Callers MUST guard ``is None`` —
    every persistence path in this module does (fail-open).
    """
    global _redis_client
    if _redis_client is None:
        from rugmunchbot._vendor.redis_client import get_redis

        _redis_client = get_redis()
    return _redis_client


@dataclass
class Hit:
    """One search result."""

    doc_id: str
    score: float
    text: str
    metadata: dict[str, Any]


class ANNIndex:
    """Per-collection vector index. Holds vectors in memory, persists to Redis.

    Usage:
        idx = ANNIndex("scam_intel")
        idx.add("doc1", vector, {"source": "x"}, text="...")
        hits = idx.search(query_vec, top_k=5)
    """

    def __init__(self, collection: str, dim: int = EMBEDDING_DIM) -> None:
        self.collection = collection
        self.dim = dim
        self._ids: list[str] = []
        # O(1) doc_id -> index lookup, avoids O(N) .index() calls
        self._id_to_idx: dict[str, int] = {}
        self._matrix: np.ndarray | None = None  # shape (N, dim), L2-normalized
        self._meta: dict[str, dict[str, Any]] = {}  # doc_id -> {text, metadata}
        self._loaded = False
        # Dirty-tracking: only persisted docs that changed
        self._dirty_ids: set[str] = set()
        self._meta_dirty = False

    # ── Redis keys ────────────────────────────────────────────────────
    def _key_docs(self) -> str:
        return f"rag:ann:{self.collection}:docs"

    def _key_meta(self) -> str:
        return f"rag:ann:{self.collection}:meta"

    # ── Persistence (dirty-tracked: only writes changed docs) ─────────
    def _persist(self, changed_ids: set[str] | None = None) -> None:
        """Write dirty docs to Redis. When called from add/delete the
        changed_ids set limits writes. On flush_all, writes everything.

        Fail-open: ``_get_redis()`` may legitimately return None (Redis
        unreachable / auth misconfig — ``app.core.redis.get_redis`` returns
        None instead of raising). A None client is skipped with a warning;
        the in-memory index stays authoritative for this process and the
        Qdrant primary write path (``engine._qdrant_upsert_chunk``) is
        unaffected. A Redis runtime error mid-write is also caught and the
        dirty set is kept so ``flush_all()`` can retry — this method never
        raises into the ingest loop.
        """
        dirty = changed_ids if changed_ids is not None else self._dirty_ids
        if not dirty and not self._meta_dirty:
            return

        r = _get_redis()
        if r is None:
            log.warning(
                "ann_persist_skipped_redis_unavailable collection=%s dirty=%d",
                self.collection,
                len(dirty),
            )
            return

        try:
            pipe = r.pipeline()
            for doc_id in dirty:
                if doc_id not in self._id_to_idx:
                    continue
                idx = self._id_to_idx[doc_id]
                entry = {
                    "vector": (self._matrix[idx].tolist() if self._matrix is not None else []),
                    "metadata": self._meta[doc_id].get("metadata", {}),
                    "text": self._meta[doc_id].get("text", ""),
                }
                try:
                    blob = json.dumps(entry)
                except (TypeError, ValueError):
                    # Non-serializable metadata must not abort the ingest —
                    # skip this doc, keep the rest of the batch.
                    log.warning(
                        "ann_persist_skip_doc collection=%s doc_id=%s err=non_serializable",
                        self.collection,
                        doc_id,
                    )
                    continue
                pipe.hset(self._key_docs(), doc_id, blob)

            if self._meta_dirty:
                pipe.set(
                    self._key_meta(),
                    json.dumps({"count": len(self._ids), "dim": self.dim}),
                )
                self._meta_dirty = False

            if dirty:
                pipe.execute()
                self._dirty_ids -= dirty
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            # Fail-open: keep dirty docs so flush_all() can retry; never
            # raise into the ingest loop (Qdrant primary write unaffected).
            self._meta_dirty = True
            log.warning("ann_persist_failed collection=%s err=%s", self.collection, e)

    def _load(self) -> None:
        if self._loaded:
            return
        r = _get_redis()
        if r is None:
            log.debug("ann_load_skipped_redis_unavailable collection=%s", self.collection)
            self._loaded = True
            return
        try:
            raw = r.hgetall(self._key_docs())
        except Exception as e:
            log.warning("ann_load_failed collection=%s err=%s", self.collection, e)
            raw = {}

        if not raw:
            self._loaded = True
            return

        ids: list[str] = []
        id_to_idx: dict[str, int] = {}
        vecs: list[list[float]] = []
        meta: dict[str, dict[str, Any]] = {}
        for i, (doc_id, blob) in enumerate(raw.items()):
            try:
                entry = json.loads(blob)
                ids.append(doc_id)
                id_to_idx[doc_id] = i
                vecs.append(entry.get("vector", []))
                meta[doc_id] = {
                    "metadata": entry.get("metadata", {}),
                    "text": entry.get("text", ""),
                }
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                log.debug("ann_skip doc=%s err=%s", doc_id, e)
        if vecs:
            self._matrix = np.asarray(vecs, dtype=np.float32)
        self._ids = ids
        self._id_to_idx = id_to_idx
        self._meta = meta
        self._loaded = True
        self._dirty_ids.clear()
        self._meta_dirty = False
        log.info("ann_loaded collection=%s count=%d", self.collection, len(ids))

    # ── Mutations ─────────────────────────────────────────────────────
    def add(
        self,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self._load()
        arr = np.asarray(vector, dtype=np.float32)
        # Stale-matrix guard (2026-08-25): the persisted matrix may predate a
        # dim change (1024-era news_v2 vs native 2560). vstacking across
        # mismatched column counts is the exact numpy concat error that killed
        # every ingest. Discard the stale cache and rebuild from scratch —
        # the ANN index is a rebuildable cache; Qdrant holds the truth.
        if self._matrix is not None and self._matrix.shape[1] != arr.shape[0]:
            log.warning(
                "ann_dim_mismatch_reset collection=%s stored=%s incoming=%s — rebuilding",
                self.collection,
                self._matrix.shape[1],
                arr.shape[0],
            )
            self._matrix = None
            self._ids = []
            self._id_to_idx = {}
            self._meta = {}
            self._dirty_ids = set()
        if arr.shape[0] != self.dim:
            # Pad or truncate
            if arr.shape[0] >= self.dim:
                arr = arr[: self.dim]
            else:
                arr = np.concatenate([arr, np.zeros(self.dim - arr.shape[0], dtype=np.float32)])
        # L2 normalize
        n = float(np.linalg.norm(arr))
        if n > 0:
            arr = arr / n

        if doc_id in self._id_to_idx and self._matrix is not None:
            # Update - replace vector in-place
            idx = self._id_to_idx[doc_id]
            self._matrix[idx] = arr
        else:
            if self._matrix is None:
                self._matrix = arr.reshape(1, -1)
            else:
                self._matrix = np.vstack([self._matrix, arr.reshape(1, -1)])
            idx = len(self._ids)
            self._ids.append(doc_id)
            self._id_to_idx[doc_id] = idx

        self._meta[doc_id] = {"metadata": metadata or {}, "text": text}
        # Track dirty — next _persist writes only this doc, not all
        self._dirty_ids.add(doc_id)
        self._meta_dirty = True
        self._persist()

    def flush_all(self) -> None:
        """Force-write all dirty docs to Redis.
        Call before shutdown or after bulk inserts."""
        if self._dirty_ids or self._meta_dirty:
            self._persist(changed_ids=self._dirty_ids | set(self._id_to_idx.keys()))

    def delete(self, doc_id: str) -> bool:
        self._load()
        if doc_id not in self._id_to_idx:
            return False
        idx = self._id_to_idx[doc_id]
        self._ids.pop(idx)
        # Rebuild id->idx map (O(N) but delete is rare)
        self._id_to_idx = {did: i for i, did in enumerate(self._ids)}
        if self._matrix is not None and self._matrix.shape[0] > 1:
            self._matrix = np.delete(self._matrix, idx, axis=0)
        else:
            self._matrix = None
        self._meta.pop(doc_id, None)
        self._dirty_ids.discard(doc_id)
        self._meta_dirty = True
        # On delete we rewrite meta entirely (fail-open when Redis is gone)
        r = _get_redis()
        if r is None:
            log.warning(
                "ann_delete_skipped_redis_unavailable collection=%s doc_id=%s",
                self.collection,
                doc_id,
            )
            return True
        try:
            pipe = r.pipeline()
            pipe.hdel(self._key_docs(), doc_id)
            pipe.set(
                self._key_meta(),
                json.dumps({"count": len(self._ids), "dim": self.dim}),
            )
            pipe.execute()
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("ann_delete_persist_failed collection=%s doc_id=%s err=%s", self.collection, doc_id, e)
        return True

    def clear(self) -> None:
        self._ids = []
        self._id_to_idx = {}
        self._matrix = None
        self._meta = {}
        self._loaded = True
        self._dirty_ids.clear()
        self._meta_dirty = False
        r = _get_redis()
        if r is None:
            log.debug("ann_clear_skipped_redis_unavailable collection=%s", self.collection)
            return
        try:
            r.delete(self._key_docs(), self._key_meta())
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            log.warning("ann_clear_failed: %s", e)

    # ── Search ────────────────────────────────────────────────────────
    def search(
        self,
        query_vector: list[float],
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> list[Hit]:
        self._load()
        if self._matrix is None or len(self._ids) == 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        n = float(np.linalg.norm(q))
        if n > 0:
            q = q / n
        # Cosine sim = dot product on normalized vectors
        scores = self._matrix @ q
        # Top-k by score
        k = min(top_k, len(self._ids))
        # argpartition is faster than full argsort for small top_k
        idx = np.argpartition(-scores, k - 1)[:k] if k < len(scores) else np.arange(len(scores))
        idx = idx[np.argsort(-scores[idx])]
        hits: list[Hit] = []
        for i in idx:
            s = float(scores[i])
            if s < min_similarity:
                continue
            doc_id = self._ids[int(i)]
            meta = self._meta.get(doc_id, {})
            hits.append(
                Hit(
                    doc_id=doc_id,
                    score=s,
                    text=meta.get("text", ""),
                    metadata=meta.get("metadata", {}),
                )
            )
        return hits

    # ── Stats ─────────────────────────────────────────────────────────
    def count(self) -> int:
        self._load()
        return len(self._ids)


# ── Per-collection cache ─────────────────────────────────────────────
_index_cache: dict[str, ANNIndex] = {}


class MultiDimAnnIndex:
    """One ANN matrix PER (collection, dim) — true multi-vector awareness.

    The fleet legitimately runs several vector spaces side by side
    (qwen3-4b native 2560, MRL-truncated 1024 legacy, nemotron 2048 via
    API). A single-matrix index either corrupts on dim change (the numpy
    concat failure that killed every ingest) or silently discards history.
    This wrapper keeps one ANNIndex per distinct dim, routes adds/searches
    by the vector's own length, and can describe itself: which spaces
    exist, how many points, which model each belongs to.
    """

    def __init__(self, collection: str) -> None:
        self.collection = collection
        self._spaces: dict[int, ANNIndex] = {}

    def _for_dim(self, dim: int) -> ANNIndex:
        if dim not in self._spaces:
            self._spaces[dim] = ANNIndex(collection=self.collection, dim=dim)
        return self._spaces[dim]

    def add(
        self,
        doc_id: str,
        vector: list[float],
        metadata: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self._for_dim(len(vector)).add(doc_id, vector, metadata, text=text)

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        # A query only matches its own space — cross-dim comparison is
        # meaningless. Callers embed the query with the space's model.
        idx = self._spaces.get(len(query_vector))
        if idx is None:
            return []
        return idx.search(query_vector, top_k=top_k, min_similarity=min_similarity)

    def describe(self) -> list[dict[str, Any]]:
        """Self-describing inventory: what spaces exist and what they are."""
        from rugmunchbot._vendor.rag_pkg.dim_registry import model_for_dim

        out = []
        for dim, idx in sorted(self._spaces.items()):
            out.append(
                {
                    "dim": dim,
                    "model": model_for_dim(dim),
                    "points": len(idx._ids),
                }
            )
        return out


def get_index(collection: str) -> MultiDimAnnIndex:
    """Get or create the multi-space ANN index for a collection."""
    if collection not in _index_cache:
        _index_cache[collection] = MultiDimAnnIndex(collection)
    return _index_cache[collection]
