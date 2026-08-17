"""Structured per-answer tracing for the chat_ai pipeline.

Emits ONE machine-parseable JSON line per answer (structlog event
``chat_answer_trace``) carrying everything needed to audit grounding and
fabrication: the question, detected intent, tools called, data sources hit,
fabricated tokens (must be 0), end-to-end latency, LLM used, provider, RAG
grounding count and the number of Telegram parts sent.

The logger (``app.domains.telegram.rugmunchbot.support.logging.get_logger``)
renders JSON lines in production (structlog ``JSONRenderer``) — no external
tracer required. Every field is a JSON-serializable scalar/list, so the line
can be grepped/parsed directly:

    {"event": "chat_answer_trace", "question": "...", "intent": "analysis",
     "route": "backend", "tools": [...], "data_sources": [...],
     "fabricated": 0, "latency_ms": 1842.1, "llm": "rmi-backend", ...}
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.chat_ai")

# Truncation budget for free-text fields so a hostile question cannot bloat
# the log line (Telegram messages are capped at 4096 chars anyway).
_QUESTION_MAX_CHARS = 200


def trace_answer(
    *,
    question: str,
    user_id: int,
    intent: str = "chat",
    route: str = "",
    tools: list[str] | None = None,
    sources: list[str] | None = None,
    fabricated_symbols: list[str] | None = None,
    fabricated_addresses: list[str] | None = None,
    latency_ms: float = 0.0,
    llm: str = "",
    provider: str = "",
    rag_count: int = 0,
    parts: int = 0,
    ok: bool = True,
) -> None:
    """Log one structured trace line for a chat answer (JSON in production).

    ``route`` is the synthesis route (``backend`` / ``local`` /
    ``tools`` / ``local_intent`` / ``redirect``); ``tools`` are the bot-side
    tools that ran (incl. ``rag_lookup``); ``sources`` are the real data
    source labels hit; ``fabricated_symbols`` / ``fabricated_addresses`` are
    the tokens the zero-fabrication guardrail stripped (must be empty).
    """
    syms = list(fabricated_symbols or [])
    addrs = list(fabricated_addresses or [])
    logger.info(
        "chat_answer_trace",
        question=question[:_QUESTION_MAX_CHARS],
        user_id=int(user_id),
        intent=intent,
        route=route,
        tools=sorted(set(tools or [])),
        data_sources=list(dict.fromkeys(sources or [])),
        fabricated_symbols=syms[:5],
        fabricated_addresses=addrs[:2],
        fabricated=len(syms) + len(addrs),
        latency_ms=round(float(latency_ms), 1),
        llm=llm[:80],
        provider=provider,
        rag_count=int(rag_count),
        parts=int(parts),
        ok=bool(ok),
    )
