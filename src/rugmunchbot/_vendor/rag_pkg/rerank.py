# VENDORED 2026-08-24 from rmi-backend/app/rag/rerank.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Cross-encoder reranking for the RAG pipeline (Qwen3 Reranker dual-tier).

Two serving tiers on Talos (llama.cpp systemd services):

* ``deep``  — Qwen3-Reranker-4B Q6_K  on 172.22.0.1:8003 (background,
  ingestion scoring, graph enrichment — quality first, latency tolerant)
* ``live``  — Qwen3-Reranker-0.6B     on 172.22.0.1:8008 (consumer-facing
  search/news — latency first). Cross-encoders need no shared vector space,
  so the live tier pairs safely with 4B-embedded corpora.

Live strain ladder: 0.6B strained/unreachable → one capped retry on the 4B
→ fail-open (caller keeps pre-rerank order = BM25/semantic ranking).
A per-endpoint strain breaker (3 failures / 2min window) stops us hammering
a server that is already under load.

Fail-open: if every tier is unavailable, callers keep the pre-rerank order
so search never breaks because a reranker is unavailable.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.tracing import trace_span

log = get_logger(__name__)

# ── Tier endpoints ──────────────────────────────────────────────────────────
# deep: Qwen3 Reranker 4B Q6_K via llama.cpp (systemd qwen-reranker.service)
RERANK_URL: str = os.getenv("RERANK_URL", "http://172.22.0.1:8003")
# live: Qwen3 Reranker 0.6B fast tier (systemd qwen-reranker-fast.service)
RERANK_URL_LIVE: str = os.getenv("RERANK_URL_FAST", "http://172.22.0.1:8008")

# ── Live-path budgets (Netcup RS: CPU-only, protect the vCPU budget) ───────
LIVE_CANDIDATE_CAP: int = int(os.getenv("RERANK_LIVE_CANDIDATE_CAP", "40"))
DEEP_FALLBACK_CAP: int = int(os.getenv("RERANK_DEEP_FALLBACK_CAP", "24"))
LIVE_TIMEOUT_S: float = float(os.getenv("RERANK_LIVE_TIMEOUT", "6.0"))
DEEP_TIMEOUT_S: float = float(os.getenv("RERANK_DEEP_TIMEOUT", "30.0"))

# ── Strain breaker (per endpoint) ───────────────────────────────────────────
_STRAIN_WINDOW_S = 120.0
_STRAIN_THRESHOLD = 3
_strain_failures: dict[str, list[float]] = defaultdict(list)


def _is_strained(url: str) -> bool:
    """True when this endpoint recently failed repeatedly — skip it."""
    now = time.monotonic()
    recent = [t for t in _strain_failures.get(url, []) if now - t < _STRAIN_WINDOW_S]
    _strain_failures[url] = recent
    return len(recent) >= _STRAIN_THRESHOLD


def _record_strain(url: str) -> None:
    _strain_failures[url].append(time.monotonic())


def clear_strain(url: str | None = None) -> None:
    """Reset strain state (success, or operator action)."""
    if url is None:
        _strain_failures.clear()
    else:
        _strain_failures.pop(url, None)


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


async def _post_rerank(
    url: str,
    query: str,
    documents: list[str],
    top_n: int,
    timeout: float,
    model_label: str,
) -> list[dict[str, Any]] | None:
    """Single POST to a reranker endpoint. ``None`` on any failure (strain
    recorded); otherwise the noise-floor-filtered result list."""
    if _is_strained(url):
        log.debug("rerank_endpoint_strained url=%s tier=%s", url, model_label)
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{url.rstrip('/')}/rerank",
                json={
                    "query": apply_instruction(query),
                    "documents": documents,
                    "top_n": min(top_n, len(documents)),
                },
            )
            response.raise_for_status()
            results = response.json().get("results", [])
        clear_strain(url)  # healthy again
        return [r for r in results if r.get("relevance_score", 0.0) >= _RERANK_NOISE_FLOOR]
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        _record_strain(url)
        log.debug("rerank_endpoint_failed url=%s model=%s err=%s", url, model_label, exc)
        return None


async def rerank_results(
    query: str,
    documents: list[str],
    top_n: int = 5,
    tier: str = "deep",
) -> list[dict[str, Any]]:
    """Rerank *documents* against *query* via the Qwen3 reranker tiers.

    Args:
        query: The user query string.
        documents: Candidate document texts (same order as retrieval).
        top_n: Maximum number of ranked results to return.
        tier: ``"live"`` (0.6B fast path, candidate-capped, strain ladder
            into the 4B) or ``"deep"`` (4B only — background quality path).

    Returns:
        List of ``{"index": int, "relevance_score": float}`` dicts ordered
        by relevance descending, filtered by the noise floor. Empty list on
        failure (fail-open → callers keep pre-rerank order). NOTE for live
        tier: indices refer to the *capped* document slice.
    """
    if not query or not documents:
        return []

    if tier == "live":
        capped = documents[:LIVE_CANDIDATE_CAP]
        with trace_span(
            "rag.rerank.qwen3_live",
            {
                "openinference.span.kind": "RERANKER",
                "openinference.embedding.model": "Qwen3-Reranker-0.6B",
                "rerank.input_count": len(capped),
                "rerank.top_n": top_n,
                "rerank.tier": "live",
            },
        ) as span:
            # 1st rung: 0.6B fast server.
            kept = await _post_rerank(RERANK_URL_LIVE, query, capped, top_n, LIVE_TIMEOUT_S, "0.6B")
            if kept is not None:
                span.set_attribute("rerank.served_by", "0.6B")
                span.set_attribute("rerank.result_count", len(kept))
                span.set_attribute("rerank.vertical", detect_vertical(query) or "default")
                return kept
            # 2nd rung: 4B deep server with a tighter candidate cap (it is
            # slower; keep its budget bounded even under live latency).
            kept = await _post_rerank(
                RERANK_URL, query, capped[:DEEP_FALLBACK_CAP], top_n, DEEP_TIMEOUT_S, "4B-fallback"
            )
            span.set_attribute("rerank.served_by", "4B-fallback" if kept is not None else "none")
            span.set_attribute("rerank.result_count", len(kept or []))
            span.set_attribute("rerank.vertical", detect_vertical(query) or "default")
            return kept or []

    # ── deep tier (default): 4B, unchanged single-shot behavior ──
    with trace_span(
        "rag.rerank.qwen3",
        {
            "openinference.span.kind": "RERANKER",
            "openinference.embedding.model": "Qwen3-Reranker-4B-Q6_K",
            "rerank.input_count": len(documents),
            "rerank.top_n": top_n,
            "rerank.tier": "deep",
        },
    ) as span:
        kept = await _post_rerank(RERANK_URL, query, documents, top_n, DEEP_TIMEOUT_S, "4B")
        span.set_attribute("rerank.result_count", len(kept or []))
        span.set_attribute("rerank.vertical", detect_vertical(query) or "default")
        return kept or []


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
    "DEEP_FALLBACK_CAP",
    "LIVE_CANDIDATE_CAP",
    "RERANK_URL",
    "RERANK_URL_LIVE",
    "clear_strain",
    "reorder_by_relevance",
    "rerank_results",
]
