# VENDORED 2026-08-17 from rmi-backend/app/rag/intelligence.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG Intelligence Enricher — query RAG to enrich scan results with past knowledge.

Provides a unified interface for querying the RAG system across multiple
collections to bring context to scans, wallet profiles, and market queries.

Architecture:
    RAGIntelligenceEnricher
    ├── find_similar_scams(token_address)   → scams + scanner + scam_intel
    ├── get_wallet_context(wallet_address)   → wallet + scanner
    ├── enrich_scan_result(scan_result)      → all collections → fused context
    └── get_market_context(query)            → news + research

Every method wraps the existing ``HybridRetriever`` and ``ContextBuilder``
from ``app.rag.retrieval``, which themselves call the M3 three-pillar
search engine (ANN + keyword + metadata filters fused via RRF).

Embedding path (Infinity):
    Retrieval embeds queries via Infinity ``/embeddings``
    (``app.rag.embedder.InfinityEmbedder``, model BAAI/bge-m3) and each
    public method finishes with one cross-encoder rerank across the
    cross-collection candidates via Infinity ``/rerank``
    (BAAI/bge-reranker-v2-m3, see ``app.rag.rerank``). Reranking is
    fail-open: if Infinity is unreachable the candidates are returned in
    pre-rerank order.

Quality metrics are logged via structlog and exposed via the feedback loop
(``app.rag.feedback``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.retrieval import ContextBuilder, HybridRetriever, ScoredHit

log = get_logger(__name__)

# ── Data types ────────────────────────────────────────────────────────────


@dataclass
class RAGContext:
    """RAG enrichment context attached to a scan result or wallet profile.

    Fields:
        similar_scams:   Past scam patterns that look similar to this token.
        wallet_context:  Labels, risk scores, and entity info about the wallet.
        market_context:  News and research relevant to this token or address.
        fusion_summary:  LLM-ready summary fusing all RAG signals.
        collections_searched: Which RAG collections contributed.
        took_ms:        Total time spent querying RAG.
        query_id:       Opaque ID for feedback tracking (see app.rag.feedback).
        error:          String if any collection query failed (non-fatal).
    """

    similar_scams: list[ScoredHit] = field(default_factory=list)
    wallet_context: list[ScoredHit] = field(default_factory=list)
    market_context: list[ScoredHit] = field(default_factory=list)
    fusion_summary: str = ""
    collections_searched: list[str] = field(default_factory=list)
    took_ms: int = 0
    query_id: str = ""
    error: str = ""


# ── Scam similarity collections (in priority order) ───────────────────────

SCAM_COLLECTIONS: list[str] = [
    "scams",  # Documented scam patterns (app.rag.ingestion.ingest_scam_pattern)
    "scanner",  # Past scan results (app.rag.ingestion.ingest_scan_result)
    "scam_intel",  # Legacy scam intelligence (app.rag.models COLLECTIONS)
    "threat_intel",  # Unified scanner + SENTINEL high-risk findings published
    #   via Redis channel ``rmi:rag:ingest`` (rmi-rag embeds into the Qdrant
    #   ``threat_intel`` collection — see publish_rag_finding). Served by the
    #   Qdrant fallback in app.rag.engine.three_pillar_search.
]

WALLET_COLLECTIONS: list[str] = [
    "wallet",  # Wallet profiles (app.rag.ingestion.ingest_wallet_profile)
    "scanner",  # Past scans that reference this wallet as deployer
]

MARKET_COLLECTIONS: list[str] = [
    "news",  # News articles (app.rag.ingestion.ingest_news_article)
    "research",  # Research posts (app.rag.ingestion.ingest_research_post)
]

# ── RAGIntelligenceEnricher ───────────────────────────────────────────────


class RAGIntelligenceEnricher:
    """Query RAG to enrich scanner results, wallet profiles, and market queries.

    Usage:
        enricher = RAGIntelligenceEnricher()
        enriched = await enricher.enrich_scan_result(scan_result)
        logger.info(enriched.rag_context.fusion_summary)

    Thread-safety: methods are async-safe; a single enricher may be shared
    across concurrent request handlers because each call creates fresh
    retriever instances (which are stateless wrappers).
    """

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        builder: ContextBuilder | None = None,
        top_k: int = 5,
    ) -> None:
        self._retriever = retriever or HybridRetriever()
        self._builder = builder or ContextBuilder()
        self._top_k = top_k

    # ── Public API ────────────────────────────────────────────────────────

    async def find_similar_scams(
        self,
        token_address: str,
        chain: str = "",
        symbol: str = "",
        name: str = "",
    ) -> list[ScoredHit]:
        """Search past scam patterns that resemble this token.

        Queries ``scams``, ``scanner``, and ``scam_intel`` collections
        using the token address, symbol, and name as search terms.

        Returns scored hits sorted by relevance (highest first).
        """
        query = self._build_scam_query(token_address, chain, symbol, name)
        all_hits: list[ScoredHit] = []

        for collection in SCAM_COLLECTIONS:
            try:
                # Per-collection rerank is disabled here: the enricher runs a
                # single cross-collection rerank below (cheaper, one API call).
                hits = await self._retriever.retrieve(
                    query=query,
                    collection=collection,
                    top_k=self._top_k,
                    expand=True,
                    rerank=False,
                )
                all_hits.extend(hits)
            except Exception as exc:
                log.debug(
                    "find_similar_scams_collection_failed",
                    collection=collection,
                    error=str(exc)[:80],
                )

        # Deduplicate by doc_id, keeping highest score
        seen: dict[str, ScoredHit] = {}
        for h in all_hits:
            key = h.doc_id or h.content[:80]
            if key not in seen or h.final_score > seen[key].final_score:
                seen[key] = h

        sorted_hits = sorted(seen.values(), key=lambda h: -h.final_score)
        # Cross-encoder rerank across the cross-collection candidates
        # (Infinity /rerank, BAAI/bge-reranker-v2-m3; fail-open).
        sorted_hits = await self._rerank_aggregated(query, sorted_hits, self._top_k * 2)
        log.info(
            "find_similar_scams",
            token=token_address[:16],
            query_len=len(query),
            total=len(all_hits),
            unique=len(sorted_hits),
        )
        return sorted_hits[: self._top_k * 2]

    async def get_wallet_context(
        self,
        wallet_address: str,
        chain: str = "",
    ) -> list[ScoredHit]:
        """Retrieve RAG context about a wallet address.

        Searches the ``wallet`` collection for profiles/labels and the
        ``scanner`` collection for any scans where this address was the
        deployer.

        Returns scored hits sorted by relevance.
        """
        query = f"wallet {wallet_address} {chain}".strip()
        all_hits: list[ScoredHit] = []

        for collection in WALLET_COLLECTIONS:
            try:
                # Try exact-address filter first, fall back to semantic
                # (per-collection rerank disabled — aggregated rerank below).
                hits = await self._retriever.retrieve(
                    query=query,
                    collection=collection,
                    top_k=self._top_k,
                    expand=False,
                    rerank=False,
                )
                all_hits.extend(hits)
            except Exception as exc:
                log.debug(
                    "get_wallet_context_collection_failed",
                    collection=collection,
                    error=str(exc)[:80],
                )

        # Deduplicate
        seen: dict[str, ScoredHit] = {}
        for h in all_hits:
            key = h.doc_id or h.content[:80]
            if key not in seen or h.final_score > seen[key].final_score:
                seen[key] = h

        sorted_hits = sorted(seen.values(), key=lambda h: -h.final_score)
        # Cross-encoder rerank across the cross-collection candidates
        # (Infinity /rerank, BAAI/bge-reranker-v2-m3; fail-open).
        sorted_hits = await self._rerank_aggregated(query, sorted_hits, self._top_k * 2)
        log.info(
            "get_wallet_context",
            wallet=wallet_address[:16],
            total=len(all_hits),
            unique=len(sorted_hits),
        )
        return sorted_hits[: self._top_k * 2]

    async def get_market_context(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[ScoredHit]:
        """Query market intelligence from RAG.

        Searches ``news`` and ``research`` collections for relevant
        articles, posts, and analysis.

        Args:
            query: Natural-language market intelligence question.
            top_k: Override the default top_k for this call.

        Returns scored hits sorted by relevance.
        """
        k = top_k or self._top_k
        all_hits: list[ScoredHit] = []

        for collection in MARKET_COLLECTIONS:
            try:
                # Per-collection rerank disabled — aggregated rerank below.
                hits = await self._retriever.retrieve(
                    query=query,
                    collection=collection,
                    top_k=k,
                    expand=True,
                    rerank=False,
                )
                all_hits.extend(hits)
            except Exception as exc:
                log.debug(
                    "get_market_context_collection_failed",
                    collection=collection,
                    error=str(exc)[:80],
                )

        # Deduplicate
        seen: dict[str, ScoredHit] = {}
        for h in all_hits:
            key = h.doc_id or h.content[:80]
            if key not in seen or h.final_score > seen[key].final_score:
                seen[key] = h

        sorted_hits = sorted(seen.values(), key=lambda h: -h.final_score)
        # Cross-encoder rerank across the cross-collection candidates
        # (Infinity /rerank, BAAI/bge-reranker-v2-m3; fail-open).
        sorted_hits = await self._rerank_aggregated(query, sorted_hits, k)
        log.info(
            "get_market_context",
            query_len=len(query),
            total=len(all_hits),
            unique=len(sorted_hits),
        )
        return sorted_hits[:k]

    async def enrich_scan_result(
        self,
        scan_result: Any,  # app.domains.scanner.models.ScanResult | app.domains.scanner.unified.model.ScanResult
    ) -> RAGContext:
        """Enrich a completed scan result with RAG context.

        Queries all relevant collections and produces a fused summary
        that captures what RAG knows about this token, its deployer,
        and similar scam patterns.

        Accepts both the legacy ``app.domains.scanner.models.ScanResult``
        (dict-style ``modules_run``) and the unified
        ``app.domains.scanner.unified.model.ScanResult`` (string-style
        ``modules_run``, deployer under ``dev_reputation``).

        Args:
            scan_result: A ScanResult dataclass instance (must have
                token_address, chain, symbol, name attributes).

        Returns:
            RAGContext dataclass with all enrichment signals.
        """
        start = time.monotonic()

        token = getattr(scan_result, "token_address", "") or ""
        chain = getattr(scan_result, "chain", "") or ""
        symbol = getattr(scan_result, "symbol", "") or ""
        name = getattr(scan_result, "name", "") or ""
        risk_flags = getattr(scan_result, "risk_flags", None) or []

        # Generate a query_id for feedback tracking
        query_id = f"enrich:{token[:16]}:{int(start * 1000)}"

        ctx = RAGContext(query_id=query_id)

        # 1. Find similar scams
        try:
            scam_hits = await self.find_similar_scams(
                token_address=token,
                chain=chain,
                symbol=symbol,
                name=name,
            )
            ctx.similar_scams = scam_hits
            ctx.collections_searched.extend(SCAM_COLLECTIONS)
        except Exception as exc:
            ctx.error = f"similar_scams: {exc!s}"
            log.warning("enrich_similar_scams_failed", token=token[:16], error=str(exc)[:100])

        # 2. If the scan result has a deployer address, get wallet context.
        #    The legacy ScanResult keeps module results in ``modules_run`` as
        #    dicts (``{"module": ..., "result": {"deployer_address": ...}}``);
        #    the unified ScanResult keeps them as module-name strings and
        #    stores the deployer under ``dev_reputation`` instead.
        deployer_addr = ""
        for mod_entry in getattr(scan_result, "modules_run", None) or []:
            if not isinstance(mod_entry, dict):
                continue
            mod_result = mod_entry.get("result") or {}
            if isinstance(mod_result, dict) and mod_result.get("deployer_address"):
                deployer_addr = mod_result["deployer_address"]
                break

        if not deployer_addr:
            dev = getattr(scan_result, "dev_reputation", None)
            if isinstance(dev, dict):
                deployer_addr = dev.get("deployer_address") or dev.get("address", "") or ""

        if deployer_addr:
            try:
                wallet_hits = await self.get_wallet_context(
                    wallet_address=deployer_addr,
                    chain=chain,
                )
                ctx.wallet_context = wallet_hits
                ctx.collections_searched.extend(WALLET_COLLECTIONS)
            except Exception as exc:
                log.warning("enrich_wallet_context_failed", deployer=deployer_addr[:16], error=str(exc)[:100])

        # 3. Get market context for this token
        market_query = f"{symbol or name or token} {chain} token security analysis".strip()
        if market_query:
            try:
                market_hits = await self.get_market_context(query=market_query)
                ctx.market_context = market_hits
                ctx.collections_searched.extend(MARKET_COLLECTIONS)
            except Exception as exc:
                log.warning("enrich_market_context_failed", token=token[:16], error=str(exc)[:100])

        # 4. Build a fusion summary
        ctx.fusion_summary = self._build_fusion_summary(ctx, token, risk_flags)

        ctx.took_ms = int((time.monotonic() - start) * 1000)
        ctx.collections_searched = list(set(ctx.collections_searched))

        log.info(
            "enrich_scan_result",
            token=token[:16],
            similar_scams=len(ctx.similar_scams),
            wallet_hits=len(ctx.wallet_context),
            market_hits=len(ctx.market_context),
            collections=ctx.collections_searched,
            took_ms=ctx.took_ms,
        )
        return ctx

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _rerank_aggregated(
        self,
        query: str,
        hits: list[ScoredHit],
        top_n: int,
    ) -> list[ScoredHit]:
        """Rerank cross-collection candidates with the Infinity cross-encoder.

        Uses the shared rerank path (``app.rag.rerank`` — Infinity
        ``/rerank`` with BAAI/bge-reranker-v2-m3): reorders hits by
        relevance, updates ``final_score``, and truncates to *top_n*.
        Fail-open: on any failure or empty response the hits are returned
        in their pre-rerank order.
        """
        if len(hits) < 2:
            return hits
        try:
            from rugmunchbot._vendor.rag_pkg.rerank import reorder_by_relevance, rerank_results

            docs = [(h.content or "")[:2000] for h in hits]
            reranked = await rerank_results(query, docs, top_n=len(hits))
            if not reranked:
                return hits
            ordered = reorder_by_relevance(hits, reranked, top_n)
            return ordered if ordered else hits
        except Exception as exc:
            log.debug("enricher_rerank_failed err=%s", exc)
            return hits

    @staticmethod
    def _build_scam_query(
        token_address: str,
        chain: str,
        symbol: str,
        name: str,
    ) -> str:
        """Build a search query that captures all identifying info about a token."""
        parts = [token_address]
        if chain:
            parts.append(chain)
        if symbol:
            parts.append(f"symbol {symbol}")
        if name:
            parts.append(name)
        parts.append("scam honeypot rug fraud")
        return " ".join(parts)

    @staticmethod
    def _build_fusion_summary(
        ctx: RAGContext,
        token_address: str,
        risk_flags: list[str],
    ) -> str:
        """Fuse all RAG signals into a single human-readable summary."""
        lines: list[str] = []
        lines.append(f"RAG Intelligence for {token_address[:16]}...")

        # Similar scams
        if ctx.similar_scams:
            lines.append(f"\nSimilar scam patterns found: {len(ctx.similar_scams)}")
            for i, h in enumerate(ctx.similar_scams[:3], 1):
                snippet = h.content[:120].replace("\n", " ")
                lines.append(f"  {i}. [{h.collection}] score={h.final_score:.3f} — {snippet}...")
        else:
            lines.append("\nNo similar scam patterns found in RAG.")

        # Wallet context
        if ctx.wallet_context:
            lines.append(f"\nDeployer context: {len(ctx.wallet_context)} signals")
            for h in ctx.wallet_context[:2]:
                snippet = h.content[:100].replace("\n", " ")
                lines.append(f"  - {snippet}...")
        else:
            lines.append("\nNo deployer context in RAG.")

        # Market context
        if ctx.market_context:
            lines.append(f"\nMarket signals: {len(ctx.market_context)} relevant documents")

        # Cross-reference current risk flags with RAG findings
        if risk_flags:
            lines.append(f"\nCurrent scan flags: {', '.join(risk_flags)}")

        # Check if RAG patterns overlap with current flags
        scam_titles = {h.metadata.get("pattern_name", "") for h in ctx.similar_scams}
        scam_titles -= {""}
        if scam_titles:
            lines.append(f"\nRAG-identified patterns: {', '.join(list(scam_titles)[:5])}")

        return "\n".join(lines)


# ── Convenience singleton ─────────────────────────────────────────────────

_default_enricher: RAGIntelligenceEnricher | None = None


def get_enricher() -> RAGIntelligenceEnricher:
    """Get the default RAGIntelligenceEnricher singleton."""
    global _default_enricher
    if _default_enricher is None:
        _default_enricher = RAGIntelligenceEnricher()
    return _default_enricher


__all__ = [
    "MARKET_COLLECTIONS",
    "SCAM_COLLECTIONS",
    "WALLET_COLLECTIONS",
    "RAGContext",
    "RAGIntelligenceEnricher",
    "get_enricher",
]
