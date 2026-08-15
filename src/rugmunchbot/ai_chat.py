#!/usr/bin/env python3
"""
RugMunchBot — DEPRECATED bot-side RAG search + LLM synthesis plumbing.

**DEPRECATED (dedup refactor):** this module is no longer imported anywhere.
The old ``handlers/message.py`` pipeline that called ``search_rag`` /
``format_rag_context`` / ``chat_synthesis`` was merged into
``handlers/chat_ai.py`` (the single free-text pipeline), and /ask now routes
through ``chat_ai.chat_ai_response`` like /chat. The ONE synthesis path is
the backend ``POST /api/v1/chat`` call (auto-RAG with news_v2 grounding).

Kept only as a historical reference and in case a bot-side RAG override is
ever needed again (it still works and is harmless). Do not wire it back into
the bot without re-visiting the dedup — prefer ``handlers/chat_ai``.

Historical usage (pre-merge):
  - handlers/message.py tier-1 chat (RAG → LLM synthesis)
  - handlers/ask.py (/ask command)

Flow:
  1. search_rag()          — parallel multi-collection search (top 3-5 hits)
  2. format_rag_context()  — render hits as a compact context block
  3. chat_synthesis()      — POST /api/v1/chat with the context injected as
                             system context; the backend LLM synthesizes a
                             single grounded answer.

Timeouts: the RAG search and LLM call can take 10-20s in total; each step
has its own budget and degrades gracefully (returns None / []) instead of
raising, so the bot never drops a chat message.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot import db
from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.prompts import (
    PERSONA_CONTEXT,
)
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = get_logger("rmi_bot")

# Collections searched for chat/ask RAG grounding.
RAG_COLLECTIONS = ["threat_intel", "rmi_agent_memory", "scam_intel", "known_scams"]

# Time budgets (seconds). The task allows RAG + LLM to take 10-20s.
RAG_SEARCH_TIMEOUT = 12.0  # per-collection HTTP timeout
RAG_OVERALL_TIMEOUT = 15.0  # upper bound for the parallel multi-collection search
LLM_SYNTHESIS_TIMEOUT = 25.0

CONTEXT_MAX_CHARS = 4000  # total context block size sent to the LLM
HIT_MAX_CHARS = 800  # per-hit snippet inside the context block

# ══════════════════════════════════════════════
# BOT PERSONALITY (v10) — "sharp degen-fluent intelligence analyst"
# ══════════════════════════════════════════════
# Owned by prompts.py (PERSONA_CONTEXT).
# The backend owns SYSTEM_PROMPT (the backend chat router); this block is
# injected on every bot→backend synthesis call via context_override, so the
# character profile applies to free-text chat, /chat, /ask and any handler
# that calls chat_synthesis().


def _build_system_context(rag_context: str | None) -> str | None:
    """Combine the personality block with any RAG grounding context."""
    parts = [PERSONA_CONTEXT]
    if rag_context:
        parts.append(rag_context)
    return "\n\n".join(parts)[:CONTEXT_MAX_CHARS]


async def search_rag(
    query: str, collections: Iterable[str] | None = None, top_k: int = 5
) -> list[dict[str, Any]]:
    """Search RAG collections in parallel, dedupe by content, sort by score.

    Returns up to ``top_k`` hits, each a dict with keys: ``content``,
    ``score``, ``metadata``, ``collection`` (added), ``doc_id``.
    Returns ``[]`` on total failure / timeout — callers fall through to the
    next chat tier.
    """
    if collections is None:
        collections = RAG_COLLECTIONS
    collections = list(collections)

    async def _search_one(collection: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=RAG_SEARCH_TIMEOUT) as client:
                r = await client.post(
                    f"{BACKEND_URL}/api/v1/rag/v2/search",
                    headers=backend_headers(),
                    json={
                        "query": query,
                        "collection": collection,
                        "top_k": top_k,
                        "min_similarity": 0.0,
                        "filters": {},
                    },
                )
                if r.status_code == 200:
                    raw_hits = r.json().get("hits", [])
                    if isinstance(raw_hits, list):
                        hits = [dict(h) for h in raw_hits if isinstance(h, dict)]
                        for h in hits:
                            # Keep the backend's truthful resolved-collection
                            # label when present; only fall back to the
                            # requested name for unlabeled hits.
                            h.setdefault("collection", collection)
                        return hits
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
            logger.debug("RAG search failed for %s: %s", collection, e)
        return []

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_search_one(c) for c in collections), return_exceptions=True),
            timeout=RAG_OVERALL_TIMEOUT,
        )
    except TimeoutError:
        logger.debug("RAG search timed out (query=%.60s)", query)
        return []

    all_hits: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, list):
            all_hits.extend(res)

    # Dedupe by content prefix, keep the highest-score copy.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for h in sorted(all_hits, key=lambda x: x.get("score", 0), reverse=True):
        content = (h.get("content") or "").strip()[:100]
        if content and content not in seen:
            seen.add(content)
            deduped.append(h)
    result = deduped[:top_k]
    if result:
        db.record_activity(rag=True)
    return result


def format_rag_context(hits: list[dict[str, Any]], max_chars: int = CONTEXT_MAX_CHARS) -> str:
    """Render top RAG hits as a compact, LLM-friendly context block.

    Each hit is labeled with its collection + score so the LLM can weigh
    source quality. Truncates to ``max_chars`` total.
    """
    parts: list[str] = []
    total = 0
    for i, h in enumerate(hits, 1):
        content = (h.get("content") or "").strip()
        if not content:
            continue
        snippet = content[:HIT_MAX_CHARS]
        col = h.get("collection") or "unknown"
        block = f"[{i}] Source: {col} (score: {h.get('score', 0):.3f})\n{snippet}"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


async def chat_synthesis(
    message: str,
    rag_context: str | None = None,
    user_id: int | None = None,
    timeout_secs: float = LLM_SYNTHESIS_TIMEOUT,
    history: Iterable[dict[str, str]] | None = None,
) -> str | None:
    """POST /api/v1/chat with the user's message + RAG context as system context.

    Returns the LLM-synthesized reply text, or ``None`` on any failure/timeout.

    - ``rag_context`` is injected via ``context_override.system_context``,
      which the backend appends to the system prompt (the backend chat
      router). The v8 persona block (PERSONA_CONTEXT from prompts.py) is
      always prepended so every reply speaks in the analyst voice.
    - ``history`` (optional, chronological user/assistant pairs) OVERRIDES the
      backend DB history so the shared "anon" bucket never contaminates bot
      responses. Without it, ``[]`` is sent (existing behavior).
    - ``user_id`` (when known) is forwarded as ``X-Forwarded-For`` so the
      backend per-IP chat rate limiter keys per Telegram user instead of
      collapsing all bot traffic onto the host IP.
    """
    payload: dict[str, Any] = {
        "message": message[:2000],
        "history": list(history) if history else [],
        "tier": "T2",
        "max_tokens": 1024,
    }
    # v8: personality block is ALWAYS injected (even without RAG context);
    # RAG grounding is appended after it when available.
    payload["context_override"] = {"system_context": _build_system_context(rag_context)}

    headers = backend_headers()
    if user_id is not None:
        headers["X-Forwarded-For"] = str(user_id)

    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            r = await client.post(f"{BACKEND_URL}/api/v1/chat", headers=headers, json=payload)
            if r.status_code == 200:
                body = r.json()
                text = (body.get("message") or "").strip()
                if text:
                    # Track AI token spend for /admin_stats (best effort).
                    usage = body.get("usage") or {}
                    tokens = int(usage.get("total_tokens") or usage.get("totalTokens") or 0)
                    if tokens:
                        db.record_activity(tokens=tokens)
                    return text
            logger.debug("chat synthesis HTTP %d (user=%s)", r.status_code, user_id)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("chat synthesis failed (user=%s): %s", user_id, e)
    return None
