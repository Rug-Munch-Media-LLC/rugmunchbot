"""LLM-call clients + endpoint/budget constants + fallback orchestration.

Extracted from ``chat_ai/__init__.py`` (Batch 11: clients; Batch 12: the
fallback orchestration): the backend RAG chat client (``POST /api/v1/chat``),
the local llama.cpp client (Qwen3.5 4B / 9B), the
endpoint/budget constants these clients use, and ``_build_fallback_answer``
+ ``_gather_fallback_context`` — the backend-less answer path that assembles
the fail-open bridge/daily/scan context + live tool data into the local
llama.cpp fallback chain.

Pipeline ordering (see ``chat_ai/__init__.py``): backend auto-RAG is
tier-1, local llama.cpp is the fallback. The local client accepts
the bridge ``sources`` list so ``rag_count`` reflects the REAL number of
grounding sources instead of a hardcoded 0.

MONKEYPATCH CONTRACT: the pipeline tests replace ``chat_ai._call_local_llm``
on the package, so the fallback chain resolves it through the package module
at call time instead of its own globals.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Any

import httpx

import rugmunchbot.handlers.chat_ai as chat_ai
from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.handlers.chat_ai._format import _format_tool_answer
from rugmunchbot.handlers.chat_ai._live_tools import (
    LIVE_TOOLS_WAIT,
    TOOL_ANSWER_EXTRA_WAIT,
    _run_live_tools_bounded,
)
from rugmunchbot.prompts import FALLBACK_SYSTEM_PROMPT, PERSONA_CONTEXT
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.intent import build_tool_prompt_block

if TYPE_CHECKING:
    from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS — BACKEND RAG CHAT (tier-1)
# ══════════════════════════════════════════════
# Backend auto-RAG synthesis: POST /api/v1/chat runs its own RAG grounding
# (news_v2) and reports the real hit count via the X-RAG-Context header.
CHAT_ENDPOINT = f"{BACKEND_URL}/api/v1/chat"
CHAT_TIMEOUT = 60.0
MAX_MESSAGE_CHARS = 2000  # backend ChatRequest.message limit

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS — LOCAL MODELS
# ══════════════════════════════════════════════
# Qwen3.5-4B for quick chat (fast, conversational; alias qwen-chat)
# Bridge gateway — containers can't reach host 127.0.0.1
LLAMA_HOST = os.getenv("LLAMA_HOST", "127.0.0.1")
QWEN_CHAT_URL = f"http://{LLAMA_HOST}:8085/v1/chat/completions"
QWEN_MODEL = "qwen-chat"
QWEN_TIMEOUT = (
    45.0  # CPU LLM: generation dominates latency (~7 tok/s); 45s covers a 200-token answer
)

# Qwen3.5-9B for deep analysis (alias qwen-analysis; hides thinking output)
QWEN_ANALYSIS_URL = f"http://{LLAMA_HOST}:8086/v1/chat/completions"
QWEN_ANALYSIS_MODEL = "qwen-analysis"
QWEN_ANALYSIS_TIMEOUT = (
    60.0  # halved: a 2-minute silent wait before failing to the next tier was worthless
)

# ── Complexity router (local-first) ─────────────────────────────────────
# Mirrors the backend ``_route_task`` (app/routers/chat/_rag.py): analytical /
# forensic / explanation-heavy questions ("why", "explain", "investigate",
# "trace", on-chain terms, long messages) → 9B qwen-analysis; simple /
# grounded questions (greeting / price / scan / short) → 4B qwen-chat. The
# local client orders its model attempts by intent so the RIGHT model answers
# first (4B for quick chat, 9B for depth) instead of always 4B-first.
_LOCAL_ANALYSIS_QUESTION_RE = re.compile(
    r"\b(?:why|explain|how does|compare|analyze|investigate|trace|what happened|deep)\b",
    re.IGNORECASE,
)
_LOCAL_ANALYSIS_TERMS = ("on-chain", "forensic", "mev", "rug", "whale", "exploit")
_LOCAL_ANALYSIS_LENGTH_THRESHOLD = 120  # chars — mirrors backend _route_task

# Context ceiling for the local llama.cpp models (8k ctx each). Beyond
# ~16k estimated tokens the local window would overflow/truncate. Estimated
# as chars/4 (a conservative English token proxy).
LOCAL_CTX_LIMIT_TOKENS = 16_000


def _route_local_model(message: str) -> str:
    """Route ``message`` to a local model tier: ``"chat"`` (4B qwen-chat) or
    ``"analysis"`` (9B qwen-analysis). Mirrors the backend
    ``_route_task`` so bot and backend agree (chat=4B / analysis=9B)."""
    low = message.lower().strip()
    if not low:
        return "chat"
    if len(low) > _LOCAL_ANALYSIS_LENGTH_THRESHOLD:
        return "analysis"
    if _LOCAL_ANALYSIS_QUESTION_RE.search(low):
        return "analysis"
    if any(term in low for term in _LOCAL_ANALYSIS_TERMS):
        return "analysis"
    return "chat"


def _local_context_too_large(
    message: str,
    history: list[dict[str, str]] | None,
    system_context: str | None,
) -> bool:
    """Estimate whether the assembled local prompt would exceed the local
    context window (``LOCAL_CTX_LIMIT_TOKENS``). Beyond it the 8k-ctx llama
    models truncate/garbage. Conservative proxy: tokens ≈ chars / 4."""
    total_chars = len(FALLBACK_SYSTEM_PROMPT) + len(message) + len(system_context or "")
    for msg in history or []:
        total_chars += len(str(msg.get("content") or ""))
    return total_chars // 4 > LOCAL_CTX_LIMIT_TOKENS


async def _local_host_loaded() -> bool:
    """Best-effort llama.cpp host-load probe (both servers busy → host loaded).

    GETs ``/health`` on both local servers with a short timeout; loaded when
    BOTH report ``slots_idle == 0`` (every slot processing). Fails OPEN to
    ``False`` on any error/timeout so a probe hiccup never blocks the local
    tier (and unit-test httpx fakes without ``get`` just fall through).
    """
    urls = (f"http://{LLAMA_HOST}:8085/health", f"http://{LLAMA_HOST}:8086/health")
    loaded = True
    for url in urls:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(0.5, connect=0.3, read=0.5)
            ) as client:
                r = await client.get(url)
            if r.status_code != 200:
                loaded = False
                continue
            data = r.json() if isinstance(r.json(), dict) else {}
            # slots_idle == 0 on a server → every slot busy → host loaded.
            # Missing field defaults to 1 (assume free slot → not loaded).
            if int(data.get("slots_idle", 1)) > 0:
                loaded = False
        except (
            httpx.HTTPError,
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            TypeError,
            AttributeError,
        ):
            # Unreachable/broken probe — never block local on it.
            return False
    return loaded


async def _call_backend_chat(
    message: str,
    user_id: int,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Backend RAG chat — ``POST /api/v1/chat`` (auto-RAG, news_v2 grounding).

    Tier-1 synthesis path: the backend runs its own RAG retrieval. The sharp
    grounded persona (``PERSONA_CONTEXT``) is shipped via
    ``context_override.system_context`` so the production model gets the
    grounding bullets + anti-hallucination rules + response-formatting
    instead of answering with the generic backend SYSTEM_PROMPT alone. The
    backend appends this block to its own prompt (``_build_messages``).
    The real RAG hit count is parsed from the ``X-RAG-Context`` response
    header and returned as ``rag_count`` so the "⚡ Grounded in N
    intelligence sources" footer renders truthfully. Per-user rate limiting
    is keyed by ``X-Forwarded-For`` (the Telegram user id).

    Returns a dict with ``message``, ``rag_count``, ``tokens``, ``model``,
    ``provider`` and ``latency_ms``, or ``None`` on any failure/timeout.
    """
    payload: dict[str, Any] = {
        "message": message[:MAX_MESSAGE_CHARS],
        "history": history or [],
        # Output budget for the backend RAG synthesis. 768 is enough for a
        # full Telegram answer (long ones are chunked bot-side anyway) and
        # cuts backend generation latency; the /chat route accepts 64..8192.
        "max_tokens": 768,
        # The #1 intelligence gap: without this, ~90% of answers use the
        # generic backend SYSTEM_PROMPT, not the sharp grounded persona.
        "context_override": {"system_context": PERSONA_CONTEXT},
    }
    headers = backend_headers()
    headers["X-Forwarded-For"] = str(user_id)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(CHAT_TIMEOUT, connect=5.0, read=CHAT_TIMEOUT, write=10.0)
        ) as client:
            r = await client.post(CHAT_ENDPOINT, headers=headers, json=payload)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("backend chat unavailable (user=%s): %s", user_id, e)
        return None
    if r.status_code != 200:
        logger.debug("backend chat HTTP %d (user=%s)", r.status_code, user_id)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    answer = (raw.get("message") or "").strip()
    if not answer:
        return None
    try:
        rag_count = int(r.headers.get("X-RAG-Context", "0") or "0")
    except (TypeError, ValueError):
        rag_count = 0
    return {
        "message": answer,
        "rag_count": rag_count,
        "tokens": raw.get("usage", {}).get("total_tokens", 0),
        "model": raw.get("model") or "rmi-backend",
        "provider": "backend",
        "latency_ms": 0,
    }


async def _call_local_llm(
    message: str,
    user_id: int,
    history: list[dict[str, str]] | None = None,
    *,
    sources: list[dict[str, str]] | None = None,
    system_context: str | None = None,
) -> dict[str, Any] | None:
    """Local LLM fallback via llama.cpp servers for free chatting.

    Qwen3.5-4B for quick chat, Qwen3.5-9B for deep analysis (aliases qwen-chat / qwen-analysis).
    Thinking output is stripped from responses.
    ``sources`` are the bridge grounding sources prepended to the prompt;
    ``rag_count = len(sources)`` so the grounding footer renders the REAL
    count instead of a hardcoded 0.
    ``system_context`` is a real-time digest (daily market/news context)
    appended to the SYSTEM prompt so the model treats it as current data.
    """
    # ONE shared condensed prompt for the local fallback path — see
    # prompts.FALLBACK_SYSTEM_PROMPT.
    system_content = FALLBACK_SYSTEM_PROMPT
    # Real-time market/news digest → SYSTEM prompt (see _call_local_llm).
    if system_context:
        system_content += (
            "\n\nCURRENT MARKET DIGEST (today's real-time context — cite it "
            "when relevant, never present it as your own memory):\n"
            f"{system_context[:1400]}"
        )
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_content,
        }
    ]
    # Conversation history is REFERENCE ONLY — never continuation. A truncated
    # prior assistant answer (e.g. a list cut at 300 chars) otherwise makes the
    # model "finish" the old answer instead of answering the new question.
    # Render it as one inert block, not as real user/assistant turns, and never
    # emit a trailing bare user turn before the current question. Last 2
    # messages only (less context = faster on CPU).
    if history:
        hist_lines: list[str] = []
        for msg in history[-2:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                hist_lines.append(f"[{role}] {content[:300]}")
        if hist_lines:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "PREVIOUS CONVERSATION (reference only — do NOT "
                        "continue it, do NOT quote it, answer only the NEW "
                        "question below):\n" + "\n".join(hist_lines)
                    ),
                }
            )
    # Add current message (labeled so the model answers THIS question).
    messages.append({"role": "user", "content": f"CURRENT QUESTION: {message[:2000]}"})

    # Try each available model in order, routed by intent (mirrors the
    # backend _route_task: chat=4B qwen / analysis=9B Qwen3.5). Simple /
    # grounded questions try the fast 4B first; deep/analysis questions try
    # the 9B first. chat_template_kwargs.enable_thinking=false disables the
    # Qwen3.5 reasoning preamble so all tokens go to the actual answer.
    if _route_local_model(message) == "analysis":
        model_order = [
            (QWEN_ANALYSIS_URL, QWEN_ANALYSIS_MODEL, QWEN_ANALYSIS_TIMEOUT),
            (QWEN_CHAT_URL, QWEN_MODEL, QWEN_TIMEOUT),
        ]
    else:
        model_order = [
            (QWEN_CHAT_URL, QWEN_MODEL, QWEN_TIMEOUT),
            (QWEN_ANALYSIS_URL, QWEN_ANALYSIS_MODEL, QWEN_ANALYSIS_TIMEOUT),
        ]
    for url, model, timeout in model_order:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=5.0, read=timeout, write=10.0)
            ) as client:
                r = await client.post(
                    url,
                    json={
                        "model": model,
                        "messages": messages,
                        "max_tokens": 200,  # generation dominates CPU latency; answers are chunked anyway
                        "temperature": 0.7,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
            logger.debug("local LLM %s unavailable (user=%s): %s", model, user_id, e)
            continue
        if r.status_code != 200:
            logger.debug("local LLM %s HTTP %d (user=%s)", model, r.status_code, user_id)
            continue
        try:
            raw = r.json()
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        choices = raw.get("choices", [])
        if not choices or not isinstance(choices, list):
            continue
        answer = (choices[0].get("message", {}) or {}).get("content", "").strip()

        # Strip thinking output (legacy Qwen emitted <think> blocks; Qwen3.5 non-reasoning, kept defensively).
        # Handle BOTH closed <think>...</think> AND unclosed <think> (when the
        # token budget runs out before the model finishes reasoning).
        # First remove properly closed think blocks
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL)
        # Then remove any remaining unclosed <think> tag and everything after it
        answer = re.sub(r"<think>.*", "", answer, flags=re.DOTALL)
        answer = re.sub(r"</?answer>", "", answer)
        answer = re.sub(r"<think>", "", answer)
        answer = re.sub(r"</think>", "", answer)
        # Take only the last substantive paragraph (thinking usually comes first)
        paragraphs = [p.strip() for p in answer.split("\n") if p.strip()]
        if len(paragraphs) > 1:
            for p in reversed(paragraphs):
                if not any(
                    p.lower().startswith(prefix)
                    for prefix in [
                        "okay",
                        "let me",
                        "first",
                        "i need",
                        "i should",
                        "i will",
                        "i can",
                        "so i",
                        "well",
                        "hmm",
                        "the user",
                        "the question",
                        "i think",
                        "i believe",
                        "i understand",
                        "i need to",
                        "so the",
                        "the answer",
                        "the user asked",
                        "since they're",
                        "since the",
                        "so that's",
                        "i'll",
                        "let's",
                        "looking at",
                    ]
                ):
                    answer = p
                    break
        answer = answer.strip()
        if not answer:
            continue
        return {
            "message": answer,
            "rag_count": len(sources or []),
            "tokens": raw.get("usage", {}).get("total_tokens", 0),
            "model": model,
            "provider": "local",
            "latency_ms": 0,
        }
    return None


async def _gather_fallback_context(
    daily_ctx_task: asyncio.Task[Any],
    scan_ctx_task: asyncio.Task[Any] | None,
    bridge_ctx_task: asyncio.Task[Any],
) -> tuple[Any, Any, Any]:
    """Await the fallback context fetches (fail-open) for the local fallback path.

    The backend tier-1 call runs CONCURRENTLY with these tasks and is awaited
    FIRST; this helper is only invoked when the backend returned ``None`` and
    the fallback path actually needs the bridge/daily/scan context. Exceptions
    are swallowed per-task (``return_exceptions=True``) so a failing fetcher
    degrades to ``None`` instead of killing the fallback LLM call.
    """
    tasks: list[asyncio.Task[Any]] = [daily_ctx_task, bridge_ctx_task]
    if scan_ctx_task is not None:
        tasks.append(scan_ctx_task)
    results = [
        r if not isinstance(r, BaseException) else None
        for r in await asyncio.gather(*tasks, return_exceptions=True)
    ]
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
) -> tuple[
    dict[str, Any] | None, list[dict[str, str]], list[dict[str, str]], ToolResult | None, bool
]:
    """Backend-less answer path: gather the fail-open context fetches that
    ground the local llama.cpp fallback chain.

    Returns ``(result, bridge_sources, bridge_actions, rag_result,
    has_market_context)``. The bridge/daily/scan context and the direct RAG
    lookup are gathered first (each fails open to ``None``), then the prompt
    is assembled from bridge blocks + scan summary + LIVE tool data, and the
    local llama.cpp chain runs with ``rag_count = len(sources)``. When
    the whole LLM chain is down but tools returned REAL data, a tools-only
    answer is produced. ``result`` is ``None`` only when every path failed
    (the caller then falls through to the canned tier-2 reply).
    """
    bridge_sources: list[dict[str, str]] = []
    bridge_actions: list[dict[str, str]] = []
    rag_result: ToolResult | None = None
    has_market_context = False

    daily_context, scan_summary, bridge_ctx = await _gather_fallback_context(
        daily_ctx_task, scan_ctx_task, bridge_ctx_task
    )
    if bridge_ctx:
        bridge_context, bridge_sources, bridge_actions = bridge_ctx
    else:
        bridge_context = None

    # Direct RAG knowledge lookup (bot-side, fail-open): searches the intel
    # DB for knowledge questions BEFORE the web/LLM fallback — the missing
    # grounding layer when the bridge is down. Real hits are prepended FIRST
    # (own data before web) and their sources feed the honest grounding
    # footer (rag_count = len(sources)).
    if rag_task is not None:
        rag_result = await rag_task

    # Live tool data — the ONLY numbers the fallback LLMs may cite. Bounded
    # wait (shielded): web_search's knowledge fallback can take up to ~50s
    # when the bridge + SearXNG are down — it must never stall the fallback
    # answer. Whatever finished in time is real data; late results are
    # re-joined by the guardrail shield and the no-LLM tool-answer window
    # below.
    fallback_live_results = await _run_live_tools_bounded(live_tools_task, LIVE_TOOLS_WAIT)

    # Fallback prompt: bridge blocks + scan summary + LIVE tool data
    # (message-specific real data). The daily market/news digest is NOT glued
    # to the user message — it is passed as ``system_context`` so the
    # fallback LLMs treat it as current data in the SYSTEM prompt instead of
    # part of the question.
    prompt = text
    if rag_result is not None and rag_result.ok and isinstance(rag_result.data, dict):
        rag_context_block = str(rag_result.data.get("context") or "")
        rag_sources = rag_result.data.get("sources") or []
        if isinstance(rag_sources, list):
            bridge_sources = bridge_sources + [s for s in rag_sources if isinstance(s, dict)]
        if rag_context_block:
            prompt = f"{rag_context_block}\n\n{prompt}"
    if bridge_context:
        prompt = f"{bridge_context}\n\n{prompt}"
    if scan_summary:
        prompt = f"{scan_summary}\n\n{prompt}"

    tool_block = build_tool_prompt_block(fallback_live_results)
    if tool_block:
        prompt = f"{tool_block}\n\n{prompt}"

    # The fallback answer was grounded by the daily digest (system_context)
    # and/or a bridge market block — remember it for the grounding footer.
    has_market_context = bool(daily_context) or any(
        isinstance(s, dict) and s.get("type") == "market" for s in bridge_sources
    )

    # Fallback: local CPU llama.cpp (qwen-chat 4B / qwen-analysis 9B, routed
    # by intent) — grounded with the bridge blocks + live tool data,
    # reporting rag_count = len(sources).
    result = await chat_ai._call_local_llm(
        prompt,
        user.id,
        history,
        sources=bridge_sources,
        system_context=daily_context,
    )

    # LLM chain completely down but tools returned REAL data → answer from
    # the tools themselves (no LLM, no guessing). Give the still-running
    # tools (notably web_search's knowledge fallback) a short extra window so
    # their usable answer renders instead of the generic fallback message —
    # the knowledge answer is labeled and never fabricated, so it is safe to
    # surface here.
    if not result:
        if not live_tools_task.done():
            fallback_live_results = await _run_live_tools_bounded(
                live_tools_task, TOOL_ANSWER_EXTRA_WAIT
            )
        tool_answer = _format_tool_answer(fallback_live_results)
        if tool_answer:
            result = {"message": tool_answer, "rag_count": len(bridge_sources), "provider": "tools"}

    return result, bridge_sources, bridge_actions, rag_result, has_market_context
