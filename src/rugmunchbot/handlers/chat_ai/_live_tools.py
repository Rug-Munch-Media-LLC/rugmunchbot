"""Live tool dispatch for the free-text AI chat pipeline.

Extracted from ``chat_ai/__init__.py`` during the de-godding pass: the
bot-side live tool runner (real API data via the tool registry), the direct
RAG knowledge lookup, and the bounded/shielded wait used by both the fallback
path and the answer guardrail. Every function fails open and never raises.
Also owns the live-tools timing budgets (``LIVE_TOOLS_WAIT`` /
``TOOL_ANSWER_EXTRA_WAIT``) used by ``_llm.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools import registry, run_for_message

if TYPE_CHECKING:
    from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")

# Bounded wait for bot-side live tools on the FALLBACK path. 8s captures the
# fast tools most questions hit (price, scan, market); the guardrail's
# bounded shield re-joins late-arriving results (notably web_search's slow
# knowledge fallback, up to ~50s when the bridge + SearXNG are down) without
# stalling the fallback answer.
LIVE_TOOLS_WAIT = 8.0
# Extra window for the still-running tools (notably web_search's knowledge
# fallback) when the ENTIRE LLM chain is down and the tool answer is the
# only thing we can send — better a real answer than the generic fallback.
TOOL_ANSWER_EXTRA_WAIT = 5.0


async def _run_live_tools(text: str, history: list[dict[str, str]] | None = None) -> dict[str, ToolResult]:
    """Run the tool registry for a user question (best-effort, bounded).

    Returns ``{tool_name: ToolResult}`` — possibly empty when no tools
    match the question. Every result is REAL API data (or a short-TTL cache
    of one); a tool that fails degrades to ``ok=False`` (the caller must not
    guess). Never raises.
    """
    try:
        return await run_for_message(text, history=history or [])
    except Exception as exc:
        logger.debug("live tool run failed (text %.60s): %s", text, exc)
        return {}


async def _run_rag_lookup(text: str) -> ToolResult | None:
    """Run the direct RAG knowledge lookup (best-effort, bounded, fail-open).

    Searches the intel DB for knowledge questions BEFORE any web/LLM
    fallback. Returns ``None`` only on an unexpected registry failure; the
    tool itself reports ``ok=False`` when the query is not knowledge-style
    or the search failed. Never raises.
    """
    try:
        return await registry.run("rag_lookup", query=text)
    except Exception as exc:
        logger.debug("rag_lookup tool run failed (text %.60s): %s", text, exc)
        return None


async def _run_live_tools_bounded(
    task: asyncio.Task[dict[str, ToolResult]],
    timeout: float,
    *,
    reuse_done: bool = False,
) -> dict[str, ToolResult]:
    """Wait up to ``timeout`` seconds for the live-tools task (shielded).

    The task keeps running past the timeout so its late results can be
    re-joined by the guardrail; this never raises. ``reuse_done`` mirrors the
    guardrail's re-read of an already-finished task: when the wait failed but
    the task actually completed, its result is returned instead of ``{}``.

    Cancellation is never swallowed: when THIS coroutine is cancelled
    (pipeline abort / shutdown), the ``CancelledError`` propagates so the
    answer pipeline unwinds instead of continuing with an empty result. The
    shielded tool task itself keeps running for the guardrail to re-join.
    """
    # An already-finished task returns instantly (never re-awaited) — the
    # guardrail hot path. Guarded BEFORE the wait so a real cancellation
    # below is never mistaken for a rejoin case.
    if reuse_done and task.done() and not task.cancelled():
        return task.result()
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except asyncio.CancelledError:
        raise
    except Exception:
        if reuse_done and task.done() and not task.cancelled():
            return task.result()
        return {}


__all__ = [
    "LIVE_TOOLS_WAIT",
    "TOOL_ANSWER_EXTRA_WAIT",
    "_run_live_tools",
    "_run_live_tools_bounded",
    "_run_rag_lookup",
]
