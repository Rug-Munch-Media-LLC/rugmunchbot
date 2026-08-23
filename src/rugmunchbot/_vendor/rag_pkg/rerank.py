# VENDORED 2026-08-23 from rmi-backend/app/rag/rerank.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Cross-encoder reranking for the RAG pipeline (Qwen3 Reranker 4B).

Reranks retrieved candidates with Qwen3 Reranker 4B Q6_K via llama.cpp
``/rerank`` endpoint (host systemd service, bound to 172.22.0.1:8003).

Fail-open: if the reranker is unreachable, callers keep the pre-rerank order
so search never breaks because the reranker is unavailable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.tracing import trace_span

log = get_logger(__name__)

# Qwen3 Reranker 4B Q6_K via llama.cpp on Talos (systemd qwen-reranker.service)
RERANK_URL: str = os.getenv("RERANK_URL", "http://172.22.0.1:8003")

# ── Vertical instruction library ─────────────────────────────────────────────
# Qwen3-Reranker is instruction-aware (official docs: +1-5%; FollowIR 14.84).
# Our llama.cpp build ignores the /rerank 'instruct' field, so vertical task
# framing is embedded into the query slot — validated live 2026-08-22
# (Tornado-funding evidence doc 0.001 -> 0.99 on a rug-pull query).
# Mirrors rmi-rag src/rugbrain/search/rerank.py; disable via RERANK_INSTRUCTIONS=off.
_INSTRUCTION_LIBRARY: dict[str, str] = {
    "forensics": (
        "Crypto forensics task: rank documents by their evidentiary value for "
        "assessing rug-pull and scam risk, including funding sources, wallet "
        "concentration, mint authority, liquidity lock status, and honeypot signals."
    ),
    "contract": (
        "Smart-contract security review task: rank documents by relevance to "
        "contract analysis, prioritizing exploit patterns, privileged functions, "
        "audit findings, and vulnerability disclosures."
    ),
    "tracing": (
        "Fund-tracing task: rank documents by relevance to tracing fund flows, "
        "wallet attribution, mixer usage, bridge hops, and entity resolution."
    ),
    "code": (
        "Blockchain code intelligence task: rank documents by relevance to the "
        "development question, prioritizing Solidity/Solana security semantics "
        "and concrete vulnerable patterns or fixes."
    ),
}
_VERTICAL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "forensics",
        (
            "rug",
            "rugpull",
            "rug pull",
            "scam",
            "honeypot",
            "deployer",
            "mint authority",
            "liquidity lock",
            "slippage",
            "pump and dump",
            "fraud",
            "steal",
            "drain",
        ),
    ),
    (
        "tracing",
        (
            "trace",
            "fund flow",
            "mixer",
            "tornado",
            "attribution",
            "launder",
            "bridge hop",
            "cluster",
            "entity resolution",
            "source of funds",
        ),
    ),
    (
        "contract",
        (
            "contract",
            "solidity",
            "solana program",
            "audit",
            "reentrancy",
            "overflow",
            "access control",
            "proxy",
            "upgradeable",
            "token standard",
        ),
    ),
    (
        "code",
        (
            "code",
            "function",
            "snippet",
            "implement",
            "example",
            "anchor",
            "web3.py",
            "ethers",
            "sdk",
            "how do i",
            "how to",
        ),
    ),
)
_RERANK_INSTRUCTIONS_ENABLED = os.getenv("RERANK_INSTRUCTIONS", "true").lower() != "off"
# Sigmoid P(relevant) below this is confidently-irrelevant noise.
_RERANK_NOISE_FLOOR = float(os.getenv("RERANK_NOISE_FLOOR", "0.10"))


def detect_vertical(query: str) -> str | None:
    """Classify a query into a platform vertical (forensics > tracing > contract > code)."""
    q = query.lower()
    for vertical, patterns in _VERTICAL_PATTERNS:
        if any(p in q for p in patterns):
            return vertical
    return None


def apply_instruction(query: str) -> str:
    """Embed the vertical instruction into the query text (query-slot trick)."""
    if not _RERANK_INSTRUCTIONS_ENABLED:
        return query
    vertical = detect_vertical(query)
    if vertical is None:
        return query
    return f"[TASK] {_INSTRUCTION_LIBRARY[vertical]} [QUERY] {query}"


async def rerank_results(query: str, documents: list[str], top_n: int = 5) -> list[dict[str, Any]]:
    """Rerank *documents* against *query* via Qwen3 Reranker 4B.

    Args:
        query: The user query string.
        documents: Candidate document texts (same order as retrieval).
        top_n: Maximum number of ranked results to return.

    Returns:
        List of ``{"index": int, "relevance_score": float}`` dicts ordered
        by relevance descending. Only documents with
        ``relevance_score > 0.0`` are kept (per spec). Empty list on
        failure (fail-open) or when nothing clears the threshold.
    """
    if not query or not documents:
        return []
    with trace_span(
        "rag.rerank.qwen3",
        {
            "openinference.span.kind": "RERANKER",
            "openinference.embedding.model": "Qwen3-Reranker-4B-Q6_K",
            "rerank.input_count": len(documents),
            "rerank.top_n": top_n,
        },
    ) as span:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{RERANK_URL.rstrip('/')}/rerank",
                    json={
                        "query": apply_instruction(query),
                        "documents": documents,
                        "top_n": min(top_n, len(documents)),
                    },
                )
                response.raise_for_status()
                results = response.json().get("results", [])
            # Noise floor: drop confidently-irrelevant docs (sigmoid 0-1 scores).
            kept = [r for r in results if r.get("relevance_score", 0.0) >= _RERANK_NOISE_FLOOR]
            span.set_attribute("rerank.result_count", len(kept))
            span.set_attribute("rerank.vertical", detect_vertical(query) or "default")
            return kept
        except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
            log.debug("rerank_results_unavailable err=%s", exc)
            return []


def reorder_by_relevance(items: list[Any], reranked: list[dict[str, Any]], top_n: int) -> list[Any]:
    """Reorder *items* (index-aligned with the rerank request) by Qwen3
    relevance, mutating each item's ``final_score`` and
    ``metadata["rerank_score"]``.

    Args:
        items: Retrieved hit objects (duck-typed: need ``final_score`` and
            ``metadata`` attributes).
        reranked: Output of :func:`rerank_results` (``index`` + ``relevance_score``).
        top_n: Truncate the reordered list to this many items.

    Returns:
        Reordered items (``[]`` when nothing survived the ``> 0.0`` filter).
    """
    ordered: list[Any] = []
    for item in sorted(reranked, key=lambda x: -x["relevance_score"]):
        idx = item["index"]
        if 0 <= idx < len(items):
            hit = items[idx]
            score = float(item["relevance_score"])
            hit.final_score = score
            hit.metadata = {**hit.metadata, "rerank_score": round(score, 4)}
            ordered.append(hit)
    return ordered[:top_n]


__all__ = [
    "RERANK_URL",
    "reorder_by_relevance",
    "rerank_results",
]
