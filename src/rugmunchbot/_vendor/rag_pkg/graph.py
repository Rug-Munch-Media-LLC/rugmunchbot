# VENDORED 2026-08-23 from rmi-backend/app/rag/graph.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Agentic RAG for crypto intelligence.

Routes queries through: real-time data → historical search →
cross-reference → synthesize, using a LangGraph state machine instead of
a single linear retrieval call.

Flow::

    classify → (security|market|code|news) → retrieve_* → rerank
             → synthesize (tools optional) → verify → END

The graph degrades gracefully:
  - LLM classify/synthesize fail open to heuristic/template answers.
  - Retrieval fail open to empty document lists.
  - Rerank fail open to the pre-rerank order.
  - Real-time tools never raise (see ``app.rag.tools``).

Usage:
    graph = create_crypto_rag_graph()
    result = await graph.ainvoke({"query": "is this token a honeypot?"})
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.rerank import reorder_by_relevance, rerank_results
from rugmunchbot._vendor.rag_pkg.retrieval import ContextBuilder, HybridRetriever, ScoredHit, apply_news_boosts
from rugmunchbot._vendor.rag_pkg.tools import get_gas_price, get_news, get_token_price, get_wallet_balance, scan_token

log = get_logger(__name__)


class ResearchState(TypedDict):
    """State threaded through the research graph."""

    query: str
    query_type: str  # "market", "security", "code", "news", "general"
    documents: list[dict[str, Any]]
    context: str
    answer: str
    sources: list[str]
    tool_calls: list[dict[str, Any]]


# ── Real-time tools the graph can call during synthesis ───────────────
TOOLS: dict[str, Any] = {
    "get_token_price": get_token_price,  # CoinGecko/DexScreener
    "get_gas_price": get_gas_price,  # public RPC (Etherscan-style)
    "get_wallet_balance": get_wallet_balance,  # web3_chat balances
    "scan_token": scan_token,  # RugMunchScan v2 (SENTINEL)
    "get_news": get_news,  # RAG news_v2 collection
}

# Collection per query type (maps to RAG_COLLECTIONS).
# News lives in the ``news_v2`` Qdrant collection (aliased as ``news``);
# the explicit ``_v2`` name avoids relying on alias resolution in the
# engine's Qdrant probe.
_COLLECTION_BY_TYPE: dict[str, str] = {
    "security": "scams",
    "market": "news_v2",
    "code": "scanner",
    "news": "news_v2",
}

# Query types with a dedicated retrieval branch.
_VALID_ROUTES: set[str] = {"security", "market", "code", "news"}

# Tools worth invoking per query type during synthesis (best-effort).
_TOOLS_BY_TYPE: dict[str, list[str]] = {
    "market": ["get_token_price", "get_gas_price"],
    "security": ["scan_token"],
    "news": ["get_news"],
    "code": ["scan_token"],
    "general": ["get_news"],
}

# Marker prepended to ``sources`` when an answer has no backing citation.
# Shared with ``app.routers.rag_v2`` so the router can surface low-confidence
# responses without relying on a magic string.
_UNVERIFIED_SENTINEL = "__unverified__"


# ── classify ──────────────────────────────────────────────────────────

# Keyword hints used when the LLM is unavailable (fail-open classifier).
_CLASSIFY_HINTS: dict[str, list[str]] = {
    "security": [
        "honeypot",
        "rug",
        "scam",
        "drain",
        "exploit",
        "phish",
        "malicious",
        "suspicious",
        "flagged",
        "attack",
        "vulnerab",
    ],
    "market": ["price", "volume", "market cap", "mcap", "chart", "usd", "gas", "pump", "dump", "tvl", "yield", "trend"],
    "code": ["contract", "code", "bytecode", "abi", "solidity", "function", "source", "verify", "audit", "compiler"],
    "news": ["news", "article", "headline", "announcement", "latest", "update", "report", "launch"],
}


async def _classify_with_llm(query: str) -> str:
    """Ask the LLM to classify the query. Raises on failure (caller falls back)."""
    from app.llm_config import generate_content

    raw = await generate_content(
        f"Classify this crypto research query into EXACTLY ONE category: "
        f"security, market, code, news, general.\n"
        f"Query: {query}\n\nAnswer with the category word only.",
        system="You are a query router. Reply with a single lowercase category word.",
        max_tokens=16,
        temperature=0.0,
    )
    text = (raw or "").strip().lower()
    for cat in ("security", "market", "code", "news", "general"):
        if re.search(rf"\b{cat}\b", text):
            return cat
    return "general"


def _classify_heuristic(query: str) -> str:
    """Keyword-based classification fallback (no LLM, deterministic)."""
    q = (query or "").lower()
    scores: dict[str, int] = dict.fromkeys(_CLASSIFY_HINTS, 0)
    for cat, hints in _CLASSIFY_HINTS.items():
        scores[cat] = sum(1 for h in hints if h in q)
    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else "general"


async def classify_query(state: ResearchState) -> dict[str, Any]:
    """What kind of crypto query is this? (LLM, heuristic fallback)."""
    query = state.get("query", "")
    try:
        query_type = await _classify_with_llm(query)
    except Exception:
        log.debug("classify_llm_failed_falling_back", query=query[:80])
        query_type = _classify_heuristic(query)
    log.info("rag_classify", query_type=query_type, query=query[:80])
    return {"query_type": query_type}


# ── route ─────────────────────────────────────────────────────────────


def route_query(state: ResearchState) -> str:
    """Conditional router: return the retrieval branch for this query type.

    ``general`` (and any unknown type) routes to the news branch, which
    is the broadest collection.
    """
    query_type = state.get("query_type", "general")
    return query_type if query_type in _VALID_ROUTES else "news"


# ── retrieve_* ────────────────────────────────────────────────────────


async def _retrieve_for_type(query: str, collection: str) -> list[dict[str, Any]]:
    """Hybrid retrieval with fail-open semantics. Returns list of dicts."""
    try:
        hits = await HybridRetriever().retrieve(
            query=query,
            collection=collection,
            top_k=5,
            rerank=False,  # graph's rerank node handles cross-encoder pass
        )
        return [
            {
                "content": h.content,
                "score": round(float(h.final_score or h.score), 4),
                "metadata": h.metadata,
                "collection": h.collection or collection,
                "doc_id": h.doc_id,
            }
            for h in hits
        ]
    except Exception as exc:
        log.debug("retrieve_failed", collection=collection, err=str(exc))
        return []


async def retrieve_security(state: ResearchState) -> dict[str, Any]:
    """Retrieve audit + security pattern documents."""
    docs = await _retrieve_for_type(state.get("query", ""), _COLLECTION_BY_TYPE["security"])
    return {"documents": docs}


async def retrieve_market(state: ResearchState) -> dict[str, Any]:
    """Retrieve price + on-chain market intelligence."""
    docs = await _retrieve_for_type(state.get("query", ""), _COLLECTION_BY_TYPE["market"])
    return {"documents": docs}


async def retrieve_code(state: ResearchState) -> dict[str, Any]:
    """Retrieve contract + code documents."""
    docs = await _retrieve_for_type(state.get("query", ""), _COLLECTION_BY_TYPE["code"])
    return {"documents": docs}


async def retrieve_news(state: ResearchState) -> dict[str, Any]:
    """Retrieve recent news articles."""
    docs = await _retrieve_for_type(state.get("query", ""), _COLLECTION_BY_TYPE["news"])
    return {"documents": docs}


# ── rerank ────────────────────────────────────────────────────────────


async def rerank_documents(state: ResearchState) -> dict[str, Any]:
    """Cross-encoder rerank (BGE reranker v2 m3 via Qwen3 llama.cpp server). Fail-open.

    Reorders ``documents`` by relevance and drops low-confidence hits.
    On reranker failure the pre-rerank order is kept.
    """
    docs = state.get("documents", [])
    query = state.get("query", "")
    if len(docs) < 2:
        return {"documents": docs}

    try:
        contents = [(d.get("content") or "")[:2000] for d in docs]
        reranked = await rerank_results(query, contents, top_n=len(contents))
        if reranked:
            # reorder_by_relevance mutates ScoredHit attrs — bridge dicts.
            hits = [
                ScoredHit(
                    content=d.get("content", ""),
                    score=float(d.get("score", 0.0)),
                    metadata=d.get("metadata", {}),
                    collection=d.get("collection", ""),
                    doc_id=d.get("doc_id", ""),
                )
                for d in docs
            ]
            ordered = reorder_by_relevance(hits, reranked, top_n=len(docs))
            if ordered:
                # Re-apply news boosts (freshness + entity overlap) after the
                # cross-encoder, which overwrites final_score with its own
                # relevance score. Keeps breaking/recent/entity-matched news
                # on top of the final ranking.
                ordered = apply_news_boosts(ordered, query)
                docs = [
                    {
                        "content": h.content,
                        "score": round(float(h.final_score or h.score), 4),
                        "metadata": h.metadata,
                        "collection": h.collection or "",
                        "doc_id": h.doc_id,
                    }
                    for h in ordered
                ]
    except Exception as exc:
        log.debug("rerank_failed_keeping_order err=%s", exc)

    return {"documents": docs}


# ── synthesize ────────────────────────────────────────────────────────


def _build_context(docs: list[dict[str, Any]]) -> str:
    """Assemble retrieved documents into a compact context string."""
    if not docs:
        return ""
    window = ContextBuilder().build(docs, max_tokens=4096)
    return window.formatted


async def _call_tools(query_type: str, query: str) -> list[dict[str, Any]]:
    """Best-effort real-time tool calls for this query type.

    Each call is wrapped so a failing tool cannot break synthesis.
    Returns a list of tool-call records (name, args, result/error).
    """
    calls: list[dict[str, Any]] = []
    for name in _TOOLS_BY_TYPE.get(query_type, []):
        fn = TOOLS.get(name)
        if fn is None:
            continue
        try:
            result = await fn(query)
            calls.append({"tool": name, "args": {"query": query}, "result": result})
        except Exception as exc:
            calls.append({"tool": name, "args": {"query": query}, "error": str(exc)})
    return calls


async def _synthesize_with_llm(query: str, query_type: str, context: str, tool_calls: list[dict[str, Any]]) -> str:
    """LLM synthesis from reranked context + live tool output.

    Raises on failure so the caller can fall back to a template answer.
    """
    from app.llm_config import generate_content

    tool_block = ""
    if tool_calls:
        tool_block = "\n\nLive data:\n" + json.dumps(tool_calls, default=str)[:4000]

    prompt = (
        f"Answer the crypto research question using ONLY the retrieved context "
        f"and live data. If the context does not answer it, say so.\n\n"
        f"Context:\n{context[:8000]}{tool_block}\n\n"
        f"Question: {query}\n\n"
        "Format: a concise answer (2-5 sentences), then a 'Sources:' line "
        "listing each cited document by its collection, then a 'Confidence:' "
        "line (high/medium/low)."
    )
    return await generate_content(
        prompt,
        system=(
            "You are a crypto intelligence analyst for Rug Munch Media. Be precise, "
            "cite sources, flag uncertainty. Do not invent on-chain facts."
        ),
        max_tokens=1024,
        temperature=0.3,
    )


def _template_answer(query: str, docs: list[dict[str, Any]], tool_calls: list[dict[str, Any]]) -> str:
    """Deterministic fallback when the LLM is unavailable."""
    lines = [f"Research summary for: {query}"]
    if docs:
        lines.append("")
        lines.append("Retrieved documents:")
        for i, d in enumerate(docs[:5], 1):
            content = (d.get("content") or "").replace("\n", " ")[:240]
            lines.append(f"  {i}. [{d.get('collection', 'unknown')}] {content}")
    else:
        lines.append("No retrieved documents matched the query.")
    if tool_calls:
        lines.append("")
        lines.append("Live data available (tool results were not integrated).")
    lines.append("")
    lines.append("Confidence: low (template fallback)")
    return "\n".join(lines)


async def synthesize_answer(state: ResearchState) -> dict[str, Any]:
    """Build the final response from reranked documents (+ optional tools)."""
    query = state.get("query", "")
    query_type = state.get("query_type", "general")
    docs = state.get("documents", [])
    context = _build_context(docs)

    tool_calls = await _call_tools(query_type, query)

    try:
        answer = await _synthesize_with_llm(query, query_type, context, tool_calls)
    except Exception as exc:
        log.debug("synthesize_llm_failed_using_template err=%s", exc)
        answer = _template_answer(query, docs, tool_calls)

    sources = sorted({d.get("collection", "") for d in docs if d.get("collection")})
    return {
        "context": context,
        "answer": answer,
        "sources": sources,
        "tool_calls": tool_calls,
    }


# ── verify ────────────────────────────────────────────────────────────


def _has_citations(answer: str, sources: list[str]) -> bool:
    """Cheap deterministic citation check (no second LLM call).

    Requires at least one retrieved source when the answer references
    retrieved context, OR a tool call when the answer relies on live data.
    """
    answer_mentions_data = bool(answer) and (bool(sources) or bool(answer.strip()))
    return bool(sources) or not answer_mentions_data


async def verify_sources(state: ResearchState) -> dict[str, Any]:
    """Check claims have citations; annotate when they do not.

    Never blocks the graph — it enriches ``sources`` with the
    ``_UNVERIFIED_SENTINEL`` marker when the synthesized answer has no backing
    document source, so callers can surface low-confidence responses.
    """
    sources = list(dict.fromkeys(state.get("sources", [])))
    answer = state.get("answer", "")
    tool_calls = state.get("tool_calls", [])

    verified = bool(_has_citations(answer, sources)) or bool(tool_calls)
    if not verified and answer:
        sources = [_UNVERIFIED_SENTINEL, *sources]

    log.info("rag_verify", verified=verified, sources=len(sources), answer_len=len(answer))
    return {"sources": sources}


# ── graph assembly ────────────────────────────────────────────────────


def create_crypto_rag_graph() -> Any:
    """Build the crypto research graph (state machine).

    Returns a compiled ``langgraph.graph.state.CompiledStateGraph``.
    """
    workflow = StateGraph(ResearchState)

    # Nodes
    workflow.add_node("classify", classify_query)  # What kind of crypto query?
    workflow.add_node("route", route_query)  # RPC vs RAG vs both (reserved)
    workflow.add_node("retrieve_security", retrieve_security)  # Audit + patterns
    workflow.add_node("retrieve_market", retrieve_market)  # Price + on-chain
    workflow.add_node("retrieve_code", retrieve_code)  # Contracts + docs
    workflow.add_node("retrieve_news", retrieve_news)  # Recent articles
    workflow.add_node("rerank", rerank_documents)  # BGE reranker filter
    workflow.add_node("synthesize", synthesize_answer)  # Build final response
    workflow.add_node("verify", verify_sources)  # Check citations

    # Edges
    workflow.set_entry_point("classify")
    workflow.add_conditional_edges(
        "classify",
        route_query,
        {
            "security": "retrieve_security",
            "market": "retrieve_market",
            "code": "retrieve_code",
            "news": "retrieve_news",
        },
    )
    # All retrieval paths → rerank → synthesize
    for node in ["retrieve_security", "retrieve_market", "retrieve_code", "retrieve_news"]:
        workflow.add_edge(node, "rerank")
    workflow.add_edge("rerank", "synthesize")
    workflow.add_edge("synthesize", "verify")
    workflow.add_edge("verify", END)

    return workflow.compile()


__all__ = [
    "TOOLS",
    "ResearchState",
    "classify_query",
    "create_crypto_rag_graph",
    "rerank_documents",
    "retrieve_code",
    "retrieve_market",
    "retrieve_news",
    "retrieve_security",
    "route_query",
    "synthesize_answer",
    "verify_sources",
]
