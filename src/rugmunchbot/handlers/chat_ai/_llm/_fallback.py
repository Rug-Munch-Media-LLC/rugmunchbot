"""LLM clients — backend-less fallback orchestration (fail-open).

``_gather_fallback_context`` awaits the bridge/daily/scan fetches within
a bounded window; ``_build_fallback_answer`` assembles the retrieved
context (bridge + scan + live tools + question router) into the remote
LLM fallback chain and, when the whole LLM chain is down but tools
returned REAL data, produces a tools-only answer. The seam
``chat_ai._call_remote_llm`` resolves through the chat_ai package
(monkeypatch contract).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import app.domains.telegram.rugmunchbot.handlers.chat_ai as chat_ai
from app.domains.telegram.rugmunchbot.handlers.chat_ai._format import _format_tool_answer
from app.domains.telegram.rugmunchbot.handlers.chat_ai._live_tools import (
    LIVE_TOOLS_WAIT,
    TOOL_ANSWER_EXTRA_WAIT,
    _run_live_tools_bounded,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    FALLBACK_CONTEXT_HARD_CAP,
    FALLBACK_CONTEXT_WAIT,
    RAG_LOOKUP_WAIT,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._router import _gather_context
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._sources import _untrusted_block
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.intent import build_tool_prompt_block

if TYPE_CHECKING:
    from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")


async def _gather_fallback_context(
    daily_ctx_task: asyncio.Task[Any],
    scan_ctx_task: asyncio.Task[Any] | None,
    bridge_ctx_task: asyncio.Task[Any],
    timeout: float = FALLBACK_CONTEXT_WAIT,
) -> tuple[Any, Any, Any]:
    """Await the fallback context fetches (fail-open) for the remote fallback path.

    The backend tier-1 call runs CONCURRENTLY with these tasks and is awaited
    FIRST; this helper is only invoked when the backend returned ``None`` and
    the fallback path actually needs the bridge/daily/scan context. Exceptions
    are swallowed per-task (``return_exceptions=True``) so a failing fetcher
    degrades to ``None`` instead of killing the fallback LLM call.

    BOUNDED (``timeout``): the fetchers were started at the TOP of the turn,
    concurrently with the backend — when the backend fails FAST (down in
    <1s) the bridge can still have up to 12s of its own timeout left. A slow
    context source must never stall the reply, so anything still in flight
    past the budget is skipped (it is cancelled by the turn-level finally).
    """
    tasks: list[asyncio.Task[Any]] = [daily_ctx_task, bridge_ctx_task]
    if scan_ctx_task is not None:
        tasks.append(scan_ctx_task)
    done, _pending = await asyncio.wait(tasks, timeout=timeout)
    # Do NOT cancel the pending fetchers here — the turn-level finally owns
    # their cancellation; they may still land for a later rejoin.
    results: list[Any] = []
    for t in tasks:
        if t not in done:
            results.append(None)
            continue
        try:
            r = t.result()
        except BaseException:
            r = None
        results.append(r)
    daily_context = results[0]
    bridge_ctx = results[1]
    scan_summary = results[2] if scan_ctx_task is not None else None
    return daily_context, scan_summary, bridge_ctx


async def _build_fallback_answer(
    text: str,
    user: Any,
    history: list[dict[str, str]],
    *,
    daily_ctx_task: asyncio.Task[Any],
    scan_ctx_task: asyncio.Task[Any] | None,
    bridge_ctx_task: asyncio.Task[Any],
    live_tools_task: asyncio.Task[dict[str, ToolResult]],
    rag_task: asyncio.Task[ToolResult | None] | None,
    router_ctx_task: asyncio.Task[tuple[str, list[dict[str, str]]]] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]], list[dict[str, str]], ToolResult | None, bool]:
    """Backend-less answer path: gather the fail-open context fetches that
    ground the remote LLM fallback chain.

    Returns ``(result, bridge_sources, bridge_actions, rag_result,
    has_market_context)``. The bridge/daily/scan context and the direct RAG
    lookup are gathered first (each fails open to ``None``), then the prompt
    is assembled from bridge blocks + scan summary + LIVE tool data + the
    question-type router's retrieved context (:func:`_gather_context`), and
    the remote LLM chain runs with ``rag_count = len(sources)``. When
    the whole LLM chain is down but tools returned REAL data, a tools-only
    answer is produced. ``result`` is ``None`` only when every path failed
    (the caller then falls through to the canned tier-2 reply).

    ``router_ctx_task`` is the question-type router context gather that the
    CALLER started concurrently with the backend RAG call (see
    ``_routing.py``) — awaiting a pre-started task is ~instant, so the
    5s-bounded router never adds serial latency to a fallback turn. When
    ``None`` (direct callers/tests) the router runs here, bounded as before.
    """
    bridge_sources: list[dict[str, str]] = []
    bridge_actions: list[dict[str, str]] = []
    rag_result: ToolResult | None = None
    has_market_context = False

    # ONE shared deadline for the whole context-gather phase below (bridge /
    # daily / scan + direct RAG + live tools + question router). Each stage
    # keeps its own budget as an UPPER BOUND, but the total is hard-capped at
    # FALLBACK_CONTEXT_HARD_CAP so a fast backend failure can never stack
    # 6+4+8+6=24s of context gathering before the remote LLM starts — the
    # LLM call always keeps its full turn budget.
    gather_deadline = time.monotonic() + FALLBACK_CONTEXT_HARD_CAP

    def _gather_remaining() -> float:
        return max(0.0, gather_deadline - time.monotonic())

    daily_context, scan_summary, bridge_ctx = await _gather_fallback_context(
        daily_ctx_task,
        scan_ctx_task,
        bridge_ctx_task,
        timeout=min(FALLBACK_CONTEXT_WAIT, _gather_remaining()),
    )
    if bridge_ctx:
        bridge_context, bridge_sources, bridge_actions = bridge_ctx
    else:
        bridge_context = None

    # Direct RAG knowledge lookup (bot-side, fail-open): searches the intel
    # DB for knowledge questions BEFORE the web/LLM fallback — the missing
    # grounding layer when the bridge is down. Real hits are prepended FIRST
    # (own data before web) and their sources feed the honest grounding
    # footer (rag_count = len(sources)). BOUNDED: the lookup was started at
    # the top of the turn concurrently with the backend; this wait only
    # covers the fast-fail remainder and a slow KB search must not stall the
    # reply (the shielded task keeps running for the guardrail, then the
    # turn finally cancels it).
    if rag_task is not None:
        try:
            rag_result = await asyncio.wait_for(
                asyncio.shield(rag_task),
                timeout=min(RAG_LOOKUP_WAIT, _gather_remaining()),
            )
        except TimeoutError:
            rag_result = None
        except asyncio.CancelledError:
            raise

    # Live tool data — the ONLY numbers the fallback LLMs may cite. Bounded
    # wait (shielded): web_search's knowledge fallback can take up to ~50s
    # when the bridge + SearXNG are down — it must never stall the fallback
    # answer. Whatever finished in time is real data; late results are
    # re-joined by the guardrail shield and the no-LLM tool-answer window
    # below.
    fallback_live_results = await _run_live_tools_bounded(
        live_tools_task,
        min(LIVE_TOOLS_WAIT, _gather_remaining()),
    )

    # Fallback prompt: the user question travels SEPARATELY from the
    # retrieved context (bridge blocks + scan summary + LIVE tool data +
    # direct RAG hits). Slicing the whole assembly with the question budget
    # previously cut the trailing question (context was prepended first), so
    # the remote LLM answered without ever seeing it. Each context block keeps
    # its <untrusted_context> wrapping; the blocks are joined and passed as
    # one labeled block with its own budget. The daily market/news digest is
    # NOT glued to the user message — it is passed as ``system_context`` so
    # the fallback LLMs treat it as current data in the SYSTEM prompt instead
    # of part of the question.
    question = text
    context_parts: list[str] = []
    if rag_result is not None and rag_result.ok and isinstance(rag_result.data, dict):
        rag_context_block = str(rag_result.data.get("context") or "")
        rag_sources = rag_result.data.get("sources") or []
        if isinstance(rag_sources, list):
            bridge_sources = bridge_sources + [s for s in rag_sources if isinstance(s, dict)]
        if rag_context_block:
            context_parts.append(_untrusted_block(rag_context_block))
    if bridge_context:
        context_parts.append(_untrusted_block(bridge_context))
    if scan_summary:
        context_parts.append(_untrusted_block(scan_summary))

    tool_block: str = ""
    try:
        tool_block = build_tool_prompt_block(fallback_live_results)
    except Exception as exc:
        # The tool-prompt builder is owned by the quality agent; a failure
        # there must never kill the fallback path — degrade to no tool block.
        logger.debug("fallback tool prompt block failed (user=%s): %s", user.id, exc)
    if tool_block:
        context_parts.append(_untrusted_block(tool_block))

    # Thin question router (fail-open, bounded ~5s): detect the question TYPE
    # and pull 1-2 relevant sources (price / scanner / RAG KB / news_v2) so
    # the remote LLM answers with real specifics instead of generic training
    # memory. Its context_block is appended as another untrusted block; its
    # sources merge into ``bridge_sources`` so the grounding footer +
    # ``_call_remote_fast``'s ``sources`` param report the REAL hit count.
    # The caller pre-starts this gather CONCURRENTLY with the backend RAG
    # call, so on the fallback path it is usually already finished — this
    # wait is bounded to the router budget and a slow router can never stall
    # the reply (shielded task is cancelled by the turn-level finally).
    try:
        if router_ctx_task is not None:
            router_context, router_sources = await asyncio.wait_for(
                asyncio.shield(router_ctx_task),
                timeout=min(_CONTEXT_GATHER_TIMEOUT + 1.0, _gather_remaining()),
            )
        else:
            router_context, router_sources = await _gather_context(question)
    except TimeoutError:
        logger.debug("question-router context task still running past budget (user=%s)", user.id)
        router_context, router_sources = "", []
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("question-router context gather failed (user=%s): %s", user.id, exc)
        router_context, router_sources = "", []
    if router_context:
        context_parts.append(_untrusted_block(router_context))
    if router_sources:
        bridge_sources = bridge_sources + router_sources
    context_block = "\n\n".join(context_parts) if context_parts else ""

    # The fallback answer was grounded by the daily digest (system_context)
    # and/or a bridge market block — remember it for the grounding footer.
    has_market_context = bool(daily_context) or any(
        isinstance(s, dict) and s.get("type") == "market" for s in bridge_sources
    )

    # Fallback: remote LLM chain (snappy/long, routed by intent) — grounded
    # with the bridge blocks + live tool data, reporting rag_count =
    # len(sources). Fail-open: the seam itself returns the graceful
    # unavailability message when the remote chain is down; an unexpected
    # failure in the client (even a crash inside it) still degrades to
    # ``None`` so the tools-only / canned paths below run — the user ALWAYS
    # gets an answer when any real data survived.
    try:
        result = await chat_ai._call_remote_llm(
            question,
            user.id,
            history,
            sources=bridge_sources,
            system_context=daily_context,
            context_block=context_block or None,
        )
    except Exception as exc:
        logger.warning("fallback remote LLM chain error (user=%s): %s", user.id, exc)
        result = None

    # LLM chain completely down but tools returned REAL data → answer from
    # the tools themselves (no LLM, no guessing). Give the still-running
    # tools (notably web_search's knowledge fallback) a short extra window so
    # their usable answer renders instead of the generic fallback message —
    # the knowledge answer is labeled and never fabricated, so it is safe to
    # surface here. A graceful unavailability result still gives the tools a
    # chance to supply REAL data before settling for the canned message.
    if not result or result.get("provider") == "unavailable":
        try:
            if not live_tools_task.done():
                fallback_live_results = await _run_live_tools_bounded(live_tools_task, TOOL_ANSWER_EXTRA_WAIT)
            tool_answer = _format_tool_answer(fallback_live_results)
        except Exception as exc:
            logger.warning("fallback tools-only answer failed (user=%s): %s", user.id, exc)
            tool_answer = None
        if tool_answer:
            result = {"message": tool_answer, "rag_count": len(bridge_sources), "provider": "tools"}
        elif not result:
            logger.warning(
                "fallback chain fully down — no LLM and no tool answer (user=%s)",
                user.id,
            )

    return result, bridge_sources, bridge_actions, rag_result, has_market_context
