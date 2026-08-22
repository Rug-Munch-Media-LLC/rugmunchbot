# VENDORED 2026-08-17 from rmi-backend/app/rag/retrieval.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Hybrid retrieval layer — query expansion, multi-pillar search, context assembly.

Builds on the existing M3 engine (engine.py) with higher-level constructs:

- ``HybridRetriever``  — weighted fusion of ANN vector + BM25-lite keyword search
- ``ContextBuilder``   — assembles retrieved chunks into structured LLM context
- ``RelevanceScorer`` — post-hoc relevance re-ranking with text features
- ``QueryExpander``   — generates additional search terms for better recall

Usage:
    retriever = HybridRetriever()
    hits = await retriever.retrieve("Was this token flagged as a honeypot?")
    ctx = ContextBuilder().build(hits)
    logger.debug(ctx.formatted)
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg._retrieval_entities import (  # noqa: F401
    _merge_with_graph_results,
    _metadata_entities,
    _parse_published_at,
    apply_news_boosts,
    extract_crypto_entities,
)
from rugmunchbot._vendor.rag_pkg._retrieval_scoring import (
    ContextWindow,
    QueryExpander,
    RelevanceScorer,
    ScoredHit,
)

# ── Config ───────────────────────────────────────────────────────────
# Will be imported after config module is ready
from rugmunchbot._vendor.rag_pkg.rerank import reorder_by_relevance, rerank_results
from rugmunchbot._vendor.rag_pkg.tracing import trace_span

log = get_logger(__name__)

# ── Data types (moved to _retrieval_scoring) ────────────────────────


# ── QueryExpander ────────────────────────────────────────────────────

# Simple crypto/security synonym map for query expansion. Covers the seed
# corpus domains: scam/security taxonomy, DeFi, TA, tokenomics and market
# microstructure. Values are appended space-separated to the query, so
# single words help both the dense embedding and the lexical sparse pillar.


# Multi-word phrase expansions: bigram keys (underscore-joined tokens) →
# space-separated synonym phrases. Matched against consecutive query tokens
# so e.g. "order block" recalls "supply demand zone institutional zone".


# Common crypto misspellings / variants for fuzzy expansion.





# ── RelevanceScorer ──────────────────────────────────────────────────




# ── News ranking boosts ──────────────────────────────────────────────

# Canonical entity map: ticker/name variants → one canonical lowercased
# label. Used so "BTC" in a query matches "Bitcoin" in an article.


# News collections that receive freshness/entity boosts.











# ── HybridRetriever ──────────────────────────────────────────────────




class HybridRetriever:
    """Weighted fusion of ANN vector search and BM25-lite keyword search.

    Delegates to the M3 engine's ``three_pillar_search`` under the hood,
    then applies query expansion and relevance re-scoring.

    Usage:
        retriever = HybridRetriever()
        hits = await retriever.retrieve(
            query="honeypot token high tax",
            collection="scanner",
            top_k=10,
        )
    """

    def __init__(self, expander: QueryExpander | None = None, scorer: RelevanceScorer | None = None) -> None:
        self._expander = expander or QueryExpander()
        self._scorer = scorer or RelevanceScorer()

    async def retrieve(
        self,
        query: str,
        collection: str = "scanner",
        top_k: int = 5,
        min_similarity: float = 0.0,
        filters: dict[str, Any] | None = None,
        expand: bool = True,
        rerank: bool = True,
        candidate_k: int | None = None,
    ) -> list[ScoredHit]:
        """Run hybrid retrieval with optional query expansion.

        Args:
            query: Natural-language search query.
            collection: RAG collection to search.
            top_k: Maximum results to return.
            min_similarity: Minimum similarity threshold (0-1).
            filters: Metadata filters (exact-match).
            expand: Whether to expand the query with synonyms (incl. bigram
                phrase expansion).
            rerank: Whether to rerank candidates with the Infinity
                cross-encoder (BAAI/bge-reranker-v2-m3) before returning.
                The underlying ``three_pillar_search`` ALREADY runs the
                mandatory cross-encoder rerank, so this pass only fires when
                the engine could not (single hit / reranker unreachable).
                Fail-open: if the reranker is unreachable the pre-rerank
                order is returned unchanged.
            candidate_k: Pre-rerank candidate pool size (defaults to
                ``top_k`` — behavior-preserving). When larger than ``top_k``
                the engine fetches ``candidate_k`` candidates, the reranker
                ranks them, and the result is truncated to ``top_k`` — so
                the cross-encoder sees a wider pool than the final answer.

        Returns:
            List of ScoredHit, sorted by relevance.
        """
        start = time.monotonic()

        with trace_span(
            "rag.retrieval.hybrid",
            {
                "openinference.span.kind": "RETRIEVER",
                "openinference.retriever.query": query,
                "retrieval.collection": collection,
                "retrieval.top_k": top_k,
                "retrieval.expand": expand,
                "retrieval.rerank": rerank,
            },
        ) as span:
            # 1. Optionally expand the query
            search_query = self._expander.expand(query) if expand else query

            # 2. Delegate to the M3 engine. ``candidate_k`` widens the pool
            # the mandatory engine rerank sees; the final ``top_k`` slice
            # happens after reranking below.
            from rugmunchbot._vendor.rag_pkg.engine import three_pillar_search

            engine_top_k = candidate_k or top_k
            raw = await three_pillar_search(
                query=search_query,
                collection=collection,
                top_k=engine_top_k,
                min_similarity=min_similarity,
                filters=filters,
            )

            # 3. Re-score and sort
            scored = self._scorer.rescore(raw, query)

            # 3a. The underlying three_pillar_search already runs the
            # MANDATORY cross-encoder rerank (engine._rerank_hits, fail-open)
            # and stamps each hit with metadata["rerank_score"]. Preserve that
            # canonical rerank order: re-sorting by a heuristic blend would
            # undo the cross-encoder's work. Only the legacy path that did not
            # rerank (single hit, reranker unreachable, or no engine) falls
            # through to the optional second pass below.
            engine_reranked = any((h.metadata or {}).get("rerank_score") is not None for h in scored)
            if engine_reranked:
                scored.sort(key=lambda s: -float((s.metadata or {}).get("rerank_score", 0.0)))
                for h in scored:
                    h.final_score = float((h.metadata or {}).get("rerank_score", h.final_score))

            # 3b. Graph-enhanced retrieval (best-effort): if the knowledge
            # graph is available and the query mentions entities, enrich
            # vector results with entity-verified neighbors (graph boost).
            try:
                from app.knowledge_graph.graph import KnowledgeGraph

                kg = KnowledgeGraph()
                entities = await kg.extract_entities(query)
                if entities:
                    graph_results = await kg.search_related(entities, limit=5)
                    if graph_results:
                        scored = _merge_with_graph_results(scored, graph_results)
                        span.set_attribute("retrieval.graph_results", len(graph_results))
                        span.set_attribute("retrieval.graph_boosted", True)
            except Exception as exc:
                log.debug("graph_enrich_skipped err=%s", exc)

            # 4. Optional cross-encoder rerank via Infinity (fail-open).
            # Skipped when the engine's mandatory rerank already ran
            # (rerank_score present) — avoids a duplicate Infinity call per
            # query. Graph-enriched hits added in step 3b ride along on the
            # engine rerank order (they carry an entity-match boost).
            if rerank and not engine_reranked and len(scored) > 1:
                scored = await self._apply_rerank(query, scored, top_k)

            # 4a. When a wider pre-rerank pool was requested, truncate to
            # the post-rerank ``top_k`` here — the reranker (engine or
            # Infinity) has ranked the candidates, so the top_k slice keeps
            # the most relevant docs regardless of which path ran.
            if candidate_k is not None and candidate_k > top_k:
                scored = scored[:top_k]

            # 5. News-specific ranking boosts (freshness + entity overlap).
            # Applied after the cross-encoder so the reranker stays the
            # primary relevance signal while freshness/entity tilt the final
            # order. No-op for non-news collections.
            scored = apply_news_boosts(scored, query, collection=collection)

            took_ms = int((time.monotonic() - start) * 1000)
            log.info(
                "hybrid_retrieve",
                collection=collection,
                top_k=top_k,
                results=len(scored),
                took_ms=took_ms,
                expanded=expand,
                rerank=rerank,
            )
            span.set_attribute("openinference.retriever.documents", len(scored))
            span.set_attribute("retrieval.result_count", len(scored))

        return scored

    async def _apply_rerank(self, query: str, scored: list[ScoredHit], top_n: int) -> list[ScoredHit]:
        """Rerank *scored* hits with the Infinity cross-encoder.

        Reorders hits by Infinity relevance (updating ``final_score``) and
        truncates to *top_n*. Fail-open: on any error or empty response the
        original ordering is returned unchanged.
        """
        if len(scored) < 2:
            return scored
        try:
            docs = [(h.content or "")[:2000] for h in scored]
            reranked = await rerank_results(query, docs, top_n=len(scored))
            if not reranked:
                return scored
            ordered = reorder_by_relevance(scored, reranked, top_n)
            return ordered if ordered else scored
        except Exception as exc:
            log.debug("hybrid_rerank_failed err=%s", exc)
            return scored

    async def retrieve_multi(
        self,
        query: str,
        collections: list[str] | None = None,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> dict[str, list[ScoredHit]]:
        """Search multiple collections in parallel.

        Returns:
            Dict mapping collection name to its scored hits.
        """
        from rugmunchbot._vendor.rag_pkg.models import COLLECTIONS as DEFAULT_COLLECTIONS

        targets = collections or DEFAULT_COLLECTIONS
        results: dict[str, list[ScoredHit]] = {}

        for col in targets:
            try:
                hits = await self.retrieve(
                    query=query,
                    collection=col,
                    top_k=top_k,
                    min_similarity=min_similarity,
                )
                results[col] = hits
            except Exception as e:
                log.warning("multi_retrieve_failed", collection=col, error=str(e))
                results[col] = []

        return results


# ── ContextBuilder ───────────────────────────────────────────────────

# Estimate: 1 token ≈ 4 characters for English text
_CHARS_PER_TOKEN: int = 4


class ContextBuilder:
    """Assemble retrieved chunks into structured LLM context.

    Produces a ``ContextWindow`` with:
      - A formatted string (sources with metadata, ranked by score)
      - Source tracking for citation
      - Token estimation for context window managment
    """

    def build(
        self,
        hits: list[ScoredHit] | list[dict[str, Any]],
        max_tokens: int = 4096,
        include_metadata: bool = True,
    ) -> ContextWindow:
        """Assemble hits into a single context window.

        Args:
            hits: Retrieved hits (ScoredHit objects or raw dicts).
            max_tokens: Maximum estimated token budget for the context.
            include_metadata: Include collection/source info in formatting.

        Returns:
            ContextWindow with formatted text and metadata.
        """
        start = time.monotonic()

        # Normalise to ScoredHit if raw dicts
        normalised: list[ScoredHit] = []
        for h in hits:
            if isinstance(h, ScoredHit):
                normalised.append(h)
            elif isinstance(h, dict):
                normalised.append(
                    ScoredHit(
                        content=h.get("content", h.get("text", "")) or "",
                        score=float(h.get("score", h.get("similarity", 0)) or 0),
                        metadata=h.get("metadata", {}) or {},
                        collection=h.get("collection", "") or "",
                        doc_id=h.get("doc_id", h.get("id", "")) or "",
                    )
                )

        # Sort by score descending
        normalised.sort(key=lambda s: -s.final_score if s.final_score else -s.score)

        parts: list[str] = []
        sources: list[str] = []
        total_chars = 0
        max_chars = max_tokens * _CHARS_PER_TOKEN

        for hit in normalised:
            # Estimate this hit's contribution
            estimated_chars = len(hit.content) + 200  # + metadata overhead
            if total_chars + estimated_chars > max_chars:
                break

            if include_metadata:
                tags = {
                    "source": hit.collection,
                    "score": round(hit.final_score if hit.final_score else hit.score, 3),
                }
                if hit.doc_id:
                    tags["id"] = hit.doc_id
                parts.append(
                    f"[Document from {hit.collection}]\n"
                    f"{hit.content}\n"
                    f"(score={tags['score']}, id={tags.get('id', '—')})\n"
                )
            else:
                parts.append(hit.content)

            total_chars += estimated_chars

            if hit.collection:
                sources.append(hit.collection)

        formatted = "\n---\n".join(parts)
        took_ms = int((time.monotonic() - start) * 1000)

        return ContextWindow(
            formatted=formatted,
            hits=normalised,
            total_tokens_est=total_chars // _CHARS_PER_TOKEN,
            sources=list(set(sources)),
            took_ms=took_ms,
        )

    @staticmethod
    def to_prompt(context: ContextWindow, question: str, system_prompt: str = "") -> str:
        """Render context + question into a prompt string."""
        parts = []
        if system_prompt:
            parts.append(system_prompt)
        parts.append("Relevant context:")
        parts.append(context.formatted)
        parts.append(f"Question: {question}")
        return "\n\n".join(parts)


# ── Convenience instance ─────────────────────────────────────────────

_default_retriever = HybridRetriever()
_default_scorer = RelevanceScorer()
_default_expander = QueryExpander()
_default_builder = ContextBuilder()


async def search(
    query: str,
    collection: str = "scanner",
    top_k: int = 5,
) -> list[ScoredHit]:
    """Quick single-collection search (uses default retriever)."""
    return await _default_retriever.retrieve(
        query=query,
        collection=collection,
        top_k=top_k,
    )


def expand_query(query: str) -> str:
    """Quick query expansion (uses default expander)."""
    return _default_expander.expand(query)


def build_context(
    hits: list[ScoredHit] | list[dict[str, Any]],
    max_tokens: int = 4096,
) -> ContextWindow:
    """Quick context assembly (uses default builder)."""
    return _default_builder.build(hits, max_tokens=max_tokens)
