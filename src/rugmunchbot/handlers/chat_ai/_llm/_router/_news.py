"""NEWS context gather — RAG news_v2 + concurrent live SearXNG layer."""

from __future__ import annotations

import asyncio

from rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS,
    _NEWS_THIN_THRESHOLD,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.chat_ai")


async def _gather_rag_news_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """RAG news_v2 hits via the existing ``app.rag.tools.get_news`` facade.

    Fail-open: any failure (import, timeout, empty hits) returns empty
    context so the LLM still answers from its own knowledge.
    """
    try:
        from rugmunchbot._vendor.rag_pkg.tools import get_news as _rag_get_news

        news = await asyncio.wait_for(
            _rag_get_news(question, top_k=5), timeout=_CONTEXT_GATHER_TIMEOUT
        )
    except Exception as exc:
        logger.debug("question-router news gather failed (query %.60s): %s", question, exc)
        return "", []
    if not isinstance(news, dict):
        return "", []
    hits = news.get("results")
    if not isinstance(hits, list) or not hits:
        return "", []
    lines = ["RAG NEWS (news_v2 — real articles; cite them by source/URL):"]
    sources: list[dict[str, str]] = []
    for h in hits[:5]:
        content = str(h.get("content") or "").strip()[:220]
        url = str(h.get("canonical_url") or "").strip()
        if not content:
            continue
        label = str(h.get("source") or url or "news article")[:60]
        lines.append(f"- {content} (source: {label})")
        if url:
            sources.append({"type": "news", "title": content[:60], "url": url})
    if len(lines) == 1:
        return "", []
    return "\n".join(lines)[:_CONTEXT_MAX_CHARS], sources


async def _gather_news_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """NEWS context: RAG news_v2 hits, PLUS live SearXNG results when the
    news_v2 corpus is thin (< ``_NEWS_THIN_THRESHOLD`` hits).

    Current-event questions ("what's happening", "latest news", "market
    today") need CURRENT, sourced intel — a thin RAG corpus cannot provide
    that, so the router falls through to the live web layer. Both blocks are
    plain text (the caller wraps them in the existing ``<untrusted_context>``
    delimiter) and their sources merge into the grounding footer.

    The RAG fetch and the SearXNG web layer run CONCURRENTLY (same pattern
    as ``_gather_edu_lore_context``): the news_v2 search can take ~5s on the
    live backend and the web layer answers in ~1s — never serialize the two
    (the old path could stack to ~8.5s inside a 5s router budget).
    """
    rag_task = asyncio.create_task(_gather_rag_news_context(question))
    web_task = asyncio.create_task(_llm_mod._gather_web_fallback(question))
    try:
        # FIRST_COMPLETED: the web layer answers fast while the news_v2
        # corpus search may take the full budget — never stall on the slow
        # RAG fetch. If RAG lands first with ≥2 hits the web fetch is
        # dropped; otherwise give the web layer the REST of the budget.
        done, pending = await asyncio.wait(
            {rag_task, web_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if rag_task in done and web_task in pending:
            await asyncio.wait({web_task}, timeout=max(0.0, _CONTEXT_GATHER_TIMEOUT - 0.2))
    except Exception as exc:
        logger.debug("question-router news gather failed (query %.60s): %s", question, exc)
    rag_block, rag_sources = "", []
    web_block, web_sources = "", []
    try:
        if not rag_task.cancelled() and rag_task.done():
            rag_block, rag_sources = rag_task.result()
    except Exception as exc:
        logger.debug("question-router news RAG layer failed (query %.60s): %s", question, exc)
    try:
        if not web_task.cancelled() and web_task.done():
            web_block, web_sources = web_task.result()
    except Exception as exc:
        logger.debug("question-router news web layer failed (query %.60s): %s", question, exc)
    finally:
        for t in (rag_task, web_task):
            if not t.done():
                t.cancel()
        if rag_task.cancelled() or web_task.cancelled():
            await asyncio.gather(rag_task, web_task, return_exceptions=True)
    if len(rag_sources) >= _NEWS_THIN_THRESHOLD:
        return rag_block, rag_sources
    parts = [b for b in (rag_block, web_block) if b]
    if not parts:
        return "", []
    return "\n\n".join(parts)[:_CONTEXT_MAX_CHARS], rag_sources + web_sources
