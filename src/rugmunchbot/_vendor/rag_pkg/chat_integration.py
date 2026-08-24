# VENDORED 2026-08-24 from rmi-backend/app/rag/chat_integration.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
RAG Chat Integration
====================
Augments every chat query with RAG context, source citations, web search,
and tool results. The central hub that makes the chatbot actually smart.

Flow:
  1. Detect intent (tool vs general)
  2. If tool intent: run tool + search RAG for relevant context about result
  3. If general: search RAG across news_v2 + scam_intel + wallet_labels
  4. Optionally: web search via SearXNG for current events
  5. Build system prompt with context + sources
  6. Call LLM with context
  7. Return answer with citations
"""

from __future__ import annotations

import asyncio
import os
import textwrap
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("rag_chat")

# ── RAG collections to search (in priority order) ──
CHAT_RAG_COLLECTIONS = [
    "scam_intel",  # Security patterns, scam knowledge — HIGHEST VALUE
    "news_v2",  # Our ingested Telegram news sweep
]

MAX_RAG_RESULTS_PER_COLLECTION = 3
MAX_RAG_TOTAL_RESULTS = 12
RAG_CONTEXT_MAX_CHARS = 6000

# SearXNG (self-hosted, free, no rate limits)
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://172.26.0.2:8080")  # SearXNG on fleet-infra_searxng-net
SEARXNG_AUTO = os.getenv("SEARXNG_AUTO", "true").lower() == "true"

# LLM config — uses backup account by default
OPENROUTER_KEY = os.getenv("OPENROUTER_BACKUP_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
# Use a capable free model for chat with context
CHAT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
CHAT_FALLBACK_MODEL = "deepseek/deepseek-v4-flash"  # paid, but the backup account has it
# A/B testing: second model option for quality comparison
CHAT_MODEL_B = "deepseek/deepseek-v4-flash"

# A/B test counter for "auto" alternation
_ab_counter: int = 0


def _get_chat_model() -> str:
    """Select the chat model based on the ``CHAT_MODEL_AB`` env var.

    Values:
        ``"nemotron"`` (default) — use the current Nemotron Ultra model.
        ``"deepseek"``         — use DeepSeek V4 Flash (paid but fast).
        ``"auto"``             — alternate between both on every call.

    Returns the model string (e.g. ``"nvidia/nemotron-3-ultra-550b-a55b:free"``).
    """
    global _ab_counter
    choice = os.getenv("CHAT_MODEL_AB", "nemotron").strip().lower()

    if choice == "deepseek":
        return CHAT_MODEL_B
    if choice == "auto":
        _ab_counter += 1
        return CHAT_MODEL if (_ab_counter % 2 == 1) else CHAT_MODEL_B
    # nemotron (default)
    return CHAT_MODEL


async def search_rag(query: str) -> list[dict[str, Any]]:
    """Search our RAG knowledge base across all relevant collections via rugbrain.

    Returns unified, reranked results with source info for citations.
    """
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    async def _search_one(collection: str) -> list[dict[str, Any]]:
        try:
            return await asyncio.wait_for(
                rugbrain_search(
                    query=query,
                    collections=[collection],
                    limit=MAX_RAG_RESULTS_PER_COLLECTION,
                ),
                timeout=60.0,
            )
        except (TimeoutError, Exception) as exc:
            logger.warning("rag_collection_timeout collection=%s err=%s", collection, exc)
            return []

    collection_results = await asyncio.gather(*[_search_one(c) for c in CHAT_RAG_COLLECTIONS])

    # Flatten and deduplicate results
    from collections import OrderedDict

    seen = OrderedDict()
    for results in collection_results:
        for hit in results:
            doc_id = hit.get("doc_id", "")
            if doc_id not in seen:
                seen[doc_id] = hit
    hits = list(seen.values())[:MAX_RAG_TOTAL_RESULTS]
    return hits


async def search_web(query: str) -> str:
    """Search the web via self-hosted SearXNG instance.

    Returns formatted text results or empty string if unavailable.
    """
    if not SEARXNG_AUTO:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json", "language": "en", "categories": "general,news"},
            )
            if r.status_code != 200:
                return ""
            data = r.json()
            results = data.get("results", [])[:5]
            if not results:
                return ""
            parts = []
            for res in results:
                title = res.get("title", "")
                url = res.get("url", "")
                snippet = res.get("content", "")
                if title and snippet:
                    parts.append(f"• {title}\n  {snippet[:200]}\n  Source: {url}")
            return "\n\n".join(parts) if parts else ""
    except (httpx.HTTPError, httpx.TimeoutException, ConnectionRefusedError, OSError) as e:
        logger.debug("rag_chat_websearch_failed err=%s", e)
        return ""


async def chat_with_context(
    query: str,
    *,
    tool_result: str | None = None,
    intent: str | None = None,
    tier: str = "long",
) -> dict[str, Any]:
    """Answer a chat query with RAG context, web results, and optional tool output.

    Returns:
        {
            "answer": "...",
            "sources": [...],
            "rag_hits": [...],
            "has_web_results": bool,
            "has_tool_result": bool,
        }
    """
    # ── Step 1: Gather context in parallel ──
    rag_task = search_rag(query)
    web_task = search_web(query)
    rag_hits, web_text = await asyncio.gather(rag_task, web_task)

    # ── Step 2: Format context for the LLM ──
    context_parts = []
    sources = []

    # Tool result (structured data from price/scan/news tools)
    if tool_result:
        context_parts.append(f"[TOOL RESULT]\n{tool_result[:1500]}")
        sources.append({"type": "tool", "label": intent or "onchain data"})

    # RAG results
    if rag_hits:
        rag_ctx = []
        for hit in rag_hits:
            text = hit.get("text", "")[:500]
            coll = hit.get("collection", "kb")
            score = hit.get("score", 0)
            doc_id = hit.get("doc_id", "")[:20]
            rag_ctx.append(f"[{coll}] (score={score:.2f})\n{text}\n")
            sources.append({"type": "rag", "collection": coll, "doc_id": doc_id, "score": score})
        context_parts.append("[RAG KNOWLEDGE BASE]\n" + "\n".join(rag_ctx))

    # Web results
    if web_text:
        context_parts.append("[WEB SEARCH RESULTS]\n" + web_text)
        sources.append({"type": "web", "label": "web search"})

    # Truncate context to fit budget
    combined_context = "\n\n".join(context_parts)
    if len(combined_context) > RAG_CONTEXT_MAX_CHARS:
        combined_context = combined_context[:RAG_CONTEXT_MAX_CHARS] + "\n[...truncated]"

    # ── Step 3: Build system prompt with enterprise voice ──
    today = datetime.now(UTC).strftime("%A, %B %d, %Y")
    has_web = bool(web_text)
    system_prompt = textwrap.dedent(f"""\
    You are the RugMunch crypto intelligence assistant — an institutional-grade Web3 analyst.
    Today is {today}.

    PRIORITY RULES:
    - WEB SEARCH RESULTS are the MOST CURRENT. Prioritize them over RAG data when they conflict.
    - RAG KNOWLEDGE BASE contains our ingested analysis — use it for context and depth.
    - TOOL RESULTS contain live on-chain data — use them for precise numbers.
    - When web and RAG data disagree on prices or stats, ALWAYS use web data and note both.

    RESPONSE FORMAT:
    - Begin with a concise, direct answer to the user's question (1-3 sentences).
    - Follow with structured details using bullet points or short paragraphs.
    - When citing on-chain data: include token address, chain, and confidence level.
    - When discussing risk: always flag the severity level (Critical/High/Medium/Low).
    - End with a forward-looking takeaway (1 sentence).

    CONTEXT:
    {combined_context}
    """)

    # ── Step 4: Call LLM with optional A/B model override ──
    from app.llm_config import chat_with_fallback

    chat_model = _get_chat_model()
    ab_group = "deepseek" if chat_model == CHAT_MODEL_B else "nemotron"
    logger.info("chat_model_selected model=%s ab_group=%s", chat_model, ab_group)

    answer = await chat_with_fallback(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        tier=tier,
        model_override=chat_model,
    )

    # ── Step 5: Format response ──
    response = {
        "answer": answer,
        "sources": sources,
        "rag_hits": len(rag_hits),
        "has_web_results": bool(web_text),
        "has_tool_result": bool(tool_result),
    }
    return response


async def direct_chat(query: str, *, tier: str = "snappy") -> dict[str, Any]:
    """Fast, no-context chat for simple queries. No RAG, no tools, no web.

    Use this for greetings, simple Q&A, or when speed matters over depth.
    """
    from app.llm_config import chat_with_fallback

    chat_model = _get_chat_model()
    ab_group = "deepseek" if chat_model == CHAT_MODEL_B else "nemotron"
    logger.info("chat_model_selected model=%s ab_group=%s", chat_model, ab_group)

    answer = await chat_with_fallback(
        messages=[{"role": "user", "content": query}],
        tier=tier,
        model_override=chat_model,
    )
    return {"answer": answer, "sources": [], "rag_hits": 0, "has_web_results": False, "has_tool_result": False}
