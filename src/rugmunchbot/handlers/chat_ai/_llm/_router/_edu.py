"""EDUCATION / LORE context — RAG intel KB + concurrent live web layer."""

from __future__ import annotations

import asyncio

from rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS,
    _EDU_ANSWER_HINT,
    _EDU_RAG_THIN_CHARS,
    _EDU_RAG_THIN_HITS,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.chat_ai")


async def _gather_edu_lore_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """EDUCATION / LORE context: RAG intel KB + CONCURRENT live web layer.

    Runs ``rag_lookup`` (intel KB: security_patterns / defi_exploits /
    scam_patterns) and the SearXNG web fallback CONCURRENTLY so the slow KB
    search (measured ~5s on the live backend) can never consume the whole
    router budget and starve the web layer. The web layer is MERGED when the
    KB is missing or thin (``_EDU_RAG_THIN_HITS`` / ``_EDU_RAG_THIN_CHARS``);
    a rich KB stands alone. Either way the education answer-structure hint
    (plain-language explanation → concrete example → risk/why-it-matters) is
    appended to the RETRIEVED CONTEXT block. Fail-open: any failure returns
    empty context and the LLM answers from its own knowledge.
    """
    from rugmunchbot.tools import get_registry
    from rugmunchbot.tools.intent import tool_kwargs

    spec = tool_kwargs(question, ["rag_lookup"])
    spec["rag_lookup"] = {"query": question}
    try:
        rag_task = asyncio.create_task(get_registry().run_many_kwargs(spec))
        web_task = asyncio.create_task(_llm_mod._gather_web_fallback(question))
    except Exception as exc:
        logger.debug("question-router edu/lore gather failed (query %.60s): %s", question, exc)
        return "", []
    try:
        # FIRST_COMPLETED: the live SearXNG layer answers in ~1s while the KB
        # search can take ~5s+ — never stall the answer on the slow KB. If the
        # KB completes first (fast backend, cache), its hits are used;
        # otherwise the web layer stands alone (fail-open: timeout caps the
        # total wait).
        done, pending = await asyncio.wait(
            {rag_task, web_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # RAG finished (or failed instantly via negative cache) but the web
        # fetch is still in flight — give the web layer the REST of the
        # router budget so a thin/absent KB never leaves the answer empty.
        if rag_task in done and web_task in pending:
            await asyncio.wait({web_task}, timeout=max(0.0, _CONTEXT_GATHER_TIMEOUT - 0.2))
    except Exception as exc:
        logger.debug("question-router edu/lore gather failed (query %.60s): %s", question, exc)
    # Collect whatever finished within the budget (fail-open on the rest).
    web_block, web_sources = "", []
    try:
        if not web_task.cancelled() and web_task.done():
            web_block, web_sources = web_task.result()
    except Exception as exc:
        logger.debug("question-router web layer failed (query %.60s): %s", question, exc)
    rag_block, rag_hits = "", []
    try:
        if not rag_task.cancelled() and rag_task.done():
            rag_results = rag_task.result() or {}
            rag_result = rag_results.get("rag_lookup")
            if rag_result is not None and getattr(rag_result, "ok", False):
                rag_data = rag_result.data if isinstance(rag_result.data, dict) else {}
                rag_hits = rag_data.get("hits") or []
                rag_block = str(rag_data.get("context") or "")
    except Exception as exc:
        logger.debug("question-router RAG layer failed (query %.60s): %s", question, exc)
    finally:
        for t in (rag_task, web_task):
            if not t.done():
                t.cancel()
        if rag_task.cancelled() or web_task.cancelled():
            await asyncio.gather(rag_task, web_task, return_exceptions=True)

    rag_thin = (
        not rag_block or len(rag_hits) < _EDU_RAG_THIN_HITS or len(rag_block) < _EDU_RAG_THIN_CHARS
    )
    if rag_thin and web_block:
        block = "\n\n".join(b for b in (rag_block, web_block) if b)
        sources = [
            *[
                {
                    "type": "rag_lookup",
                    "query": question[:90],
                    "title": "rag_lookup \u00b7 intel KB",
                }
            ],
            *web_sources,
        ]
        if not rag_block:
            sources = web_sources
    elif rag_block:
        block = rag_block
        sources = [
            {"type": "rag_lookup", "query": question[:90], "title": "rag_lookup \u00b7 intel KB"}
        ]
    else:
        return "", []
    # Reserve room for the hint so it always survives truncation.
    block = block[: max(0, _CONTEXT_MAX_CHARS - len(_EDU_ANSWER_HINT) - 2)]
    if not block:
        return "", []
    return f"{block}\n\n{_EDU_ANSWER_HINT}", sources
