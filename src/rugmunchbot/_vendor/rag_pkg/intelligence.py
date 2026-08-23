# VENDORED 2026-08-23 from rmi-backend/app/rag/intelligence.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG intelligence enricher — routes through rugbrain HTTP API.

Minimal replacement that preserves ``RAGContext``, ``RAGIntelligenceEnricher``,
and ``ScoredHit`` classes for backward compatibility. All searches go through
``app.integrations.rugbrain_client``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)


@dataclass
class ScoredHit:
    """A single RAG search result with score and metadata."""

    content: str = ""
    score: float = 0.0
    final_score: float | None = None
    collection: str = ""
    doc_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGContext:
    """RAG enrichment context attached to a scan result or wallet profile."""

    similar_scams: list[ScoredHit] = field(default_factory=list)
    wallet_context: list[ScoredHit] = field(default_factory=list)
    market_context: list[ScoredHit] = field(default_factory=list)
    fusion_summary: str = ""
    collections_searched: list[str] = field(default_factory=list)
    took_ms: int = 0
    query_id: str = ""
    error: str = ""


SCAM_COLLECTIONS: list[str] = [
    "known_scams",
    "defi_exploits",
    "threat_intel_v2",
    "scam_intel",
]
WALLET_COLLECTIONS: list[str] = [
    "wallet_profiles",
    "known_scams",
]
MARKET_COLLECTIONS: list[str] = [
    "news_v2",
    "research_posts",
]


class RAGIntelligenceEnricher:
    """Query rugbrain HTTP API to enrich scanner results, wallet profiles, and market queries.

    Minimal replacement for the original 505-line class. All searches
    go through ``app.integrations.rugbrain_client.rugbrain_search``.
    """

    def __init__(self, retriever: Any = None, builder: Any = None, top_k: int = 5) -> None:
        self._top_k = top_k

    async def enrich_scan_result(self, result: Any) -> RAGContext:
        """Enrich a scan result with similar scams, wallet context, and market signals."""
        from rugmunchbot._vendor.rugbrain_client import rugbrain_search

        t0 = datetime.now(UTC)
        token_address = getattr(result, "token_address", getattr(result, "address", ""))
        chain = getattr(result, "chain", "")
        symbol = getattr(result, "symbol", "")
        deployer = getattr(result, "deployer_address", getattr(result, "deployer", ""))

        query = f"{symbol} {token_address} {chain} scam pattern deployer exploit".strip()
        hits = await rugbrain_search(query=query, collections=SCAM_COLLECTIONS, limit=self._top_k * 2)
        similar_scams = [
            ScoredHit(
                content=(h.get("content") or h.get("text") or ""),
                score=float(h.get("rrf_score", 0)),
                final_score=float(h.get("rrf_score", 0)),
                collection=(h.get("_source_collections") or ["?"])[0],
                doc_id=h.get("doc_id", ""),
                metadata=h.get("metadata", {}),
            )
            for h in hits
        ]

        wallet_hits = (
            await rugbrain_search(query=f"{deployer} {chain} wallet", collections=WALLET_COLLECTIONS, limit=3)
            if deployer
            else []
        )
        wallet_context = [
            ScoredHit(
                content=(h.get("content") or h.get("text") or ""),
                score=float(h.get("rrf_score", 0)),
                collection=(h.get("_source_collections") or ["?"])[0],
            )
            for h in wallet_hits
        ]

        took_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
        return RAGContext(
            similar_scams=similar_scams[: self._top_k],
            wallet_context=wallet_context[:3],
            collections_searched=SCAM_COLLECTIONS + WALLET_COLLECTIONS,
            took_ms=took_ms,
            fusion_summary=f"Found {len(similar_scams)} similar scams, {len(wallet_context)} wallet signals in {took_ms}ms",
        )

    async def get_wallet_context(self, wallet_address: str, chain: str = "") -> list[ScoredHit]:
        """Get wallet context from rugbrain."""
        from rugmunchbot._vendor.rugbrain_client import rugbrain_search

        hits = await rugbrain_search(
            query=f"{wallet_address} {chain} wallet profile", collections=WALLET_COLLECTIONS, limit=5
        )
        return [
            ScoredHit(
                content=(h.get("content") or h.get("text") or ""),
                score=float(h.get("rrf_score", 0)),
                final_score=float(h.get("rrf_score", 0)),
                collection=(h.get("_source_collections") or ["?"])[0],
                doc_id=h.get("doc_id", ""),
                metadata=h.get("metadata", {}),
            )
            for h in hits
        ]

    async def find_similar_scams(
        self, token_address: str, chain: str = "", symbol: str = "", name: str = ""
    ) -> list[ScoredHit]:
        """Find similar scam patterns for a token."""
        from rugmunchbot._vendor.rugbrain_client import rugbrain_search

        query = f"{name} {symbol} {token_address} {chain} scam".strip()
        hits = await rugbrain_search(query=query, collections=SCAM_COLLECTIONS, limit=self._top_k)
        return [
            ScoredHit(
                content=(h.get("content") or h.get("text") or ""),
                score=float(h.get("rrf_score", 0)),
                final_score=float(h.get("rrf_score", 0)),
                collection=(h.get("_source_collections") or ["?"])[0],
                doc_id=h.get("doc_id", ""),
                metadata=h.get("metadata", {}),
            )
            for h in hits
        ]
