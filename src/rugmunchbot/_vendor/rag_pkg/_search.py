# VENDORED 2026-08-17 from rmi-backend/app/rag/_search.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Three-pillar retrieval for the M3 RAG engine.

Extracted from ``app/rag/engine.py`` during the Qdrant consolidation
de-godding pass: the primary Qdrant hybrid retrieval path, the legacy
numpy/Redis + BM25-lite pillars, RRF fusion, mandatory cross-encoder
rerank and the context-token budget. ``engine.py`` re-exports every
public name.

Module-level side effects are intentional and preserved: ``_CONTEXT_MAX_TOKENS``
and ``_DEFAULT_STORE`` read the environment at import time, exactly as the
original ``engine.py`` did.
"""

from __future__ import annotations

import os
import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg._qdrant import (
    _build_sparse_query,
    _qdrant_search,
    _resolve_qdrant_collection,
)
from rugmunchbot._vendor.rag_pkg.ann_index import Hit, get_index
from rugmunchbot._vendor.rag_pkg.chunking import estimate_tokens
from rugmunchbot._vendor.rag_pkg.config import RAG_CONTEXT_MAX_TOKENS
from rugmunchbot._vendor.rag_pkg.embeddings import current_backend, get_embedding
from rugmunchbot._vendor.rag_pkg.tracing import trace_span

log = get_logger(__name__)

# RRF constant
_RRF_K = 60

# Hard context-token budget applied AFTER rerank on the primary search
# facade (env override wins, e.g. ``RAG_CONTEXT_MAX_TOKENS=2048``).
_CONTEXT_MAX_TOKENS = int(os.getenv("RAG_CONTEXT_MAX_TOKENS", RAG_CONTEXT_MAX_TOKENS))

# Max characters sent per candidate to the cross-encoder (bge-reranker-v2-m3
# truncates to 512 tokens internally; 2000 chars is a safe upper bound).
_RERANK_MAX_CHARS = 2000

# Primary-store routing: RAG_STORE=qdrant (default) serves search through
# the unified Qdrant hybrid store (app.rag.qdrant_store); RAG_STORE=legacy
# keeps the numpy/Redis + BM25-lite pillar path. Qdrant failures fall back
# to the legacy path automatically.
_DEFAULT_STORE = os.getenv("RAG_STORE", "qdrant").strip().lower()


# ── Three-pillar search ──────────────────────────────────────────────
async def _qdrant_primary_search(
    query: str,
    qvec: list[float],
    collection: str,
    top_k: int,
    min_similarity: float,
    filters: dict[str, Any] | None,
) -> tuple[list[Hit], str]:
    """Qdrant hybrid retrieval (dense + lexical-sparse, RRF) — the primary path.

    Returns ``(hits, backend)`` where ``backend`` is ``"legacy"`` when the
    caller should run the legacy pillar path instead (Qdrant unreachable,
    error, or returned nothing). Hits are engine ``Hit`` objects; metadata
    filters are applied post-retrieval to preserve legacy semantics (the
    legacy path filtered ``hit.metadata``, not raw payload fields).
    """
    from rugmunchbot._vendor.rag_pkg.qdrant_store import PREFETCH_MULTIPLIER, get_qdrant_store

    try:
        qdrant_collection = await _resolve_qdrant_collection(collection)
        store = get_qdrant_store(qdrant_collection)
        raw = await store.search(
            vector=qvec,
            sparse_vector=_build_sparse_query(query),
            filters=filters or None,
            limit=top_k * PREFETCH_MULTIPLIER,
        )
    except Exception as exc:
        log.warning("rag_qdrant_primary_error collection=%s err=%s", collection, exc)
        return [], "legacy"

    if not raw:
        log.info("rag_qdrant_primary_empty collection=%s falling_back=legacy", collection)
        return [], "legacy"

    hits: list[Hit] = []
    for item in raw:
        score = float(item.get("score", 0.0) or 0.0)
        if min_similarity > 0 and score < min_similarity:
            continue
        hits.append(
            Hit(
                doc_id=str(item.get("doc_id", "")),
                score=score,
                text=str(item.get("text", "")),
                metadata={
                    **dict(item.get("metadata", {}) or {}),
                    # Truthful provenance: the ACTUAL Qdrant collection the
                    # hit came from (alias-resolved). Downstream ``sources``
                    # labeling must use this, never the requested name.
                    "resolved_collection": qdrant_collection,
                },
            )
        )

    # Post-filter to match legacy exact-match semantics on hit.metadata.
    filtered = _apply_filters(hits, filters or {})
    return filtered[:top_k], "qdrant"


async def _legacy_pillar_search(
    query: str,
    qvec: list[float],
    collection: str,
    top_k: int,
    min_similarity: float,
    filters: dict[str, Any] | None,
) -> list[Hit]:
    """The legacy three-pillar path: numpy ANN + BM25-lite + RRF + Qdrant fallback."""
    # Pillar 1: ANN vector search
    idx = get_index(collection)
    ann_hits = idx.search(qvec, top_k=top_k * 3, min_similarity=min_similarity)

    # Pillar 2: keyword (BM25-lite via Redis text scan on stored docs)
    keyword_hits = await _keyword_search(query, collection, top_k * 3)

    # Pillar 3: metadata filter (apply to ann+keyword results)
    _apply_filters(ann_hits + keyword_hits, filters or {})

    # RRF fusion
    fused = _reciprocal_rank_fusion([ann_hits, keyword_hits], top_k=top_k)

    # Apply filters post-fusion
    if filters:
        fused = [h for h in fused if _matches_filters(h, filters)]

    # Qdrant fallback: if the in-process index has no data for this
    # collection, consult Qdrant (e.g. `news` seeded by news_to_rag.py).
    # The requested name is resolved to the ACTUAL Qdrant collection first
    # (alias table + existence probe) so a legacy/P1-KB name like
    # ``wallet_profiles`` lands on the live ``wallet_v2`` corpus instead of
    # searching a nonexistent collection. Strictly additive — collections
    # with local data are untouched.
    if not fused:
        qdrant_collection = await _resolve_qdrant_collection(collection)
        qdrant_hits = await _qdrant_search(qvec, qdrant_collection, limit=top_k, score_threshold=min_similarity)
        if qdrant_hits:
            fused = qdrant_hits[:top_k]
            log.info(
                "rag_search_qdrant_fallback requested=%s resolved=%s hits=%d",
                collection,
                qdrant_collection,
                len(fused),
            )
    return fused


async def _rerank_hits(query: str, hits: list[Hit]) -> list[Hit]:
    """Cross-encoder rerank (Infinity, BAAI/bge-reranker-v2-m3) — MANDATORY.

    Reranks *hits* against *query* and reorders them by cross-encoder
    relevance, updating each hit's ``score`` and
    ``metadata["rerank_score"]``. This is the mandatory post-retrieval
    step on the primary search facade; there is no skip path. Fail-open:
    if the reranker is unreachable or returns nothing, the pre-rerank
    order is kept (search must never break because reranking is down).
    """
    if len(hits) < 2:
        return hits
    try:
        from rugmunchbot._vendor.rag_pkg.rerank import rerank_results

        docs = [(h.text or "")[:_RERANK_MAX_CHARS] for h in hits]
        reranked = await rerank_results(query, docs, top_n=len(hits))
        if not reranked:
            return hits
        ordered: list[Hit] = []
        for item in sorted(reranked, key=lambda x: -float(x.get("relevance_score", 0.0))):
            idx = int(item.get("index", -1))
            if 0 <= idx < len(hits):
                hit = hits[idx]
                score = float(item.get("relevance_score", 0.0))
                hit.score = score
                hit.metadata = {**hit.metadata, "rerank_score": round(score, 4)}
                ordered.append(hit)
        return ordered if ordered else hits
    except Exception as exc:
        log.debug("rag_rerank_failed err=%s", exc)
        return hits


def _apply_context_budget(hits: list[Hit], max_tokens: int = _CONTEXT_MAX_TOKENS) -> list[Hit]:
    """Truncate *hits* to a hard context-token budget.

    Walks the (reranked) hits in order, accumulating ``estimate_tokens``
    per hit, and keeps the longest prefix that fits within ``max_tokens``.
    At least one hit is always kept so a single oversized chunk still
    surfaces. This guarantees the context handed to the LLM never exceeds
    the budget.
    """
    if max_tokens <= 0:
        return hits
    budgeted: list[Hit] = []
    used = 0
    for h in hits:
        tokens = estimate_tokens(h.text or "")
        if budgeted and used + tokens > max_tokens:
            break
        used += tokens
        budgeted.append(h)
    return budgeted


async def three_pillar_search(
    query: str,
    collection: str = "scam_intel",
    top_k: int = 5,
    min_similarity: float = 0.0,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the three-pillar search and return RRF-fused, reranked hits.

    Each returned hit: {doc_id, score, text, metadata}

    Primary path (``RAG_STORE=qdrant``, default): the unified Qdrant hybrid
    store (dense + lexical-sparse prefetches, server-side RRF k=60) replaces
    the in-process numpy ANN + Redis BM25-lite scan. The Infinity bge-m3
    embedding (``get_embedding``) still runs here. Cross-encoder rerank
    (Infinity ``BAAI/bge-reranker-v2-m3``) is MANDATORY: every result list
    is reranked before a hard context-token budget (``RAG_CONTEXT_MAX_TOKENS``,
    default 4096) is applied, so the LLM never receives an unbounded
    context. Rerank is fail-open (pre-rerank order kept when the reranker
    is unreachable), but there is no skip path.
    Set ``RAG_STORE=legacy`` to restore the previous numpy/Redis path.
    """
    start = time.monotonic()

    with trace_span(
        "rag.search.three_pillar",
        {
            "openinference.span.kind": "RETRIEVER",
            "openinference.retriever.query": query,
            "retrieval.collection": collection,
            "retrieval.top_k": top_k,
        },
    ) as span:
        # Embed once — shared by the Qdrant primary path and the legacy path.
        qvec = await get_embedding(query)

        if _DEFAULT_STORE == "qdrant":
            fused, backend = await _qdrant_primary_search(query, qvec, collection, top_k, min_similarity, filters)
            if backend == "legacy":
                # Qdrant unavailable or returned nothing → legacy pillars.
                fused = await _legacy_pillar_search(query, qvec, collection, top_k, min_similarity, filters)
        else:
            backend = "legacy"
            fused = await _legacy_pillar_search(query, qvec, collection, top_k, min_similarity, filters)

        # G2: mandatory cross-encoder rerank (fail-open) + hard context-token
        # budget before the LLM call. No skip path.
        pre_rerank = len(fused)
        fused = await _rerank_hits(query, fused)
        rerank_applied = bool(fused and fused[0].metadata.get("rerank_score") is not None)
        span.set_attribute("retrieval.rerank_input", pre_rerank)
        span.set_attribute("retrieval.rerank_applied", rerank_applied)
        fused = _apply_context_budget(fused, max_tokens=_CONTEXT_MAX_TOKENS)
        span.set_attribute("retrieval.context_budget_tokens", _CONTEXT_MAX_TOKENS)

        took_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "rag_search_done collection=%s store=%s fused=%d took_ms=%d backend=%s",
            collection,
            backend,
            len(fused),
            took_ms,
            current_backend(),
        )
        span.set_attribute("openinference.retriever.documents", len(fused))
        span.set_attribute("retrieval.result_count", len(fused))
        span.set_attribute("retrieval.backend", backend)
        span.set_attribute("retrieval.store", backend)
    return [
        {
            "doc_id": h.doc_id,
            "score": h.score,
            "text": h.text,
            "metadata": h.metadata,
        }
        for h in fused
    ]


async def _keyword_search(query: str, collection: str, limit: int) -> list[Hit]:
    """Improved BM25 keyword search with proper tokenization and IDF weighting.

    Uses Okapi BM25 formula with:
      - Tokenisation: lowercase, split on non-alpha, remove stopwords + short terms
      - IDF: inverse document frequency computed from all docs in the collection
      - Length normalisation: average doc length as reference
      - BM25 parameters: k1=1.5, b=0.75 (standard defaults)

    Returns Hits with score in [0, 1] (normalized). While not as good as
    learned sparse retrieval (SPLADE), this significantly outperforms the
    previous TF/length heuristic on precision@K.
    """
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        r = get_redis()
        if r is None:
            return []
        raw = r.hgetall(f"rag:ann:{collection}:docs")
    except Exception as e:
        log.debug("keyword_search_redis_failed: %s", e)
        return []

    if not raw:
        return []

    import json
    import math
    import re

    # ── Tokenizer ──────────────────────────────────────────────────────
    # Crypto-aware stopwords: keep short token/address terms, filter noise
    _stopwords: set[str] = {
        "the",
        "this",
        "that",
        "and",
        "for",
        "with",
        "from",
        "has",
        "have",
        "was",
        "were",
        "been",
        "are",
        "not",
        "but",
        "its",
        "all",
        "can",
        "will",
        "just",
        "very",
        "also",
        "than",
        "then",
        "each",
        "which",
        "what",
        "when",
        "where",
        "how",
        "who",
    }

    # Sample large collections to prevent OOM — process up to 10K docs max
    max_keyword_docs = 10_000
    if len(raw) > max_keyword_docs:
        import random

        log.info("keyword_search_sampling collection=%s total=%d sample=%d", collection, len(raw), max_keyword_docs)
        raw = dict(random.sample(list(raw.items()), max_keyword_docs))

    def _tokenize(text: str) -> list[str]:
        """Tokenize: lowercase, split on non-alpha, filter stopwords + length."""
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_#]*", text.lower())
        return [t for t in tokens if t not in _stopwords and len(t) > 1]

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    # ── Corpus stats (single pass) ──────────────────────────────────
    doc_list: list[tuple[str, str, dict[str, Any]]] = []  # (doc_id, text, metadata)
    doc_lengths: list[int] = []
    # doc_freq[t] = number of documents containing term t
    doc_freq: dict[str, int] = {}

    for doc_id_raw, blob in raw.items():
        try:
            entry = json.loads(blob)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        text = entry.get("text", "")
        if not text:
            continue
        tokens = _tokenize(text)
        if not tokens:
            continue
        doc_id = doc_id_raw.decode() if isinstance(doc_id_raw, bytes) else doc_id_raw
        doc_list.append((doc_id, text, entry.get("metadata", {})))
        doc_lengths.append(len(tokens))
        # Unique terms per doc for IDF
        for t in set(tokens):
            doc_freq[t] = doc_freq.get(t, 0) + 1

    if not doc_list:
        return []

    n_docs = len(doc_list)
    avg_dl = sum(doc_lengths) / n_docs

    # ── BM25 scoring ─────────────────────────────────────────────────
    k1 = 1.5
    b = 0.75

    scored: list[tuple[float, str, str, dict[str, Any]]] = []
    for idx, (doc_id, text, metadata) in enumerate(doc_list):
        tokens = _tokenize(text)
        if not tokens:
            continue
        dl = doc_lengths[idx]
        # Term frequency in this doc
        tf_map: dict[str, int] = {}
        for t in tokens:
            tf_map[t] = tf_map.get(t, 0) + 1

        score = 0.0
        for t in query_tokens:
            tf = tf_map.get(t, 0)
            if tf == 0:
                continue
            df = doc_freq.get(t, 1)  # smoothing
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
            # BM25 term contribution
            term_score = idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
            score += term_score

        if score > 0:
            scored.append((score, doc_id, text, metadata))

    # ── Normalise and return ─────────────────────────────────────────
    scored.sort(key=lambda x: -x[0])
    out: list[Hit] = []
    max_score = scored[0][0] if scored else 1.0
    for score, doc_id, text, metadata in scored[:limit]:
        out.append(
            Hit(
                doc_id=doc_id,
                score=min(1.0, score / max_score) if max_score > 0 else 0.0,
                text=text,
                metadata=metadata,
            )
        )
    return out


def _apply_filters(hits: list[Hit], filters: dict[str, Any]) -> list[Hit]:
    """Pre-filter: keep hits whose metadata matches all filter key=val pairs."""
    if not filters:
        return hits
    return [h for h in hits if _matches_filters(h, filters)]


def _matches_filters(hit: Hit, filters: dict[str, Any]) -> bool:
    return all(hit.metadata.get(k) == v for k, v in filters.items())


def _reciprocal_rank_fusion(pillar_hits: list[list[Hit]], top_k: int, k: int = _RRF_K) -> list[Hit]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    RRF score(d) = sum( 1 / (k + rank_i(d)) ) for each pillar that contains d.
    """
    scores: dict[str, float] = {}
    by_id: dict[str, Hit] = {}
    for pillar in pillar_hits:
        for rank, h in enumerate(pillar, start=1):
            scores[h.doc_id] = scores.get(h.doc_id, 0.0) + 1.0 / (k + rank)
            if h.doc_id not in by_id:
                by_id[h.doc_id] = h

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    out: list[Hit] = []
    for doc_id, rrf_score in ranked[:top_k]:
        h = by_id[doc_id]
        # Normalize to [0, 1] roughly - RRF max is ~3/k for 3 pillars
        norm = min(1.0, rrf_score * k / 3.0)
        out.append(
            Hit(
                doc_id=h.doc_id,
                score=norm,
                text=h.text,
                metadata=h.metadata,
            )
        )
    return out


__all__ = [
    "_CONTEXT_MAX_TOKENS",
    "_DEFAULT_STORE",
    "_RERANK_MAX_CHARS",
    "_RRF_K",
    "_apply_context_budget",
    "_apply_filters",
    "_keyword_search",
    "_legacy_pillar_search",
    "_matches_filters",
    "_qdrant_primary_search",
    "_reciprocal_rank_fusion",
    "_rerank_hits",
    "three_pillar_search",
]
