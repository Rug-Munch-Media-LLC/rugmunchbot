"""Answer-quality helpers for the free-text AI chat pipeline.

Extracted from ``chat_ai/_helpers.py`` during the de-godding pass: the
post-processing guards (think-strip + degenerate-output detection) and the
truthful grounding-source counter. Pure, unit-testable — applied in
``chat_ai_response`` before the answer is stored/sent so the user and the
chat history never see a reasoning chain or a degenerate token-repetition
loop. ``_helpers.py`` re-imports everything it needs from here.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

# Single shared implementation — see ``app.core.ai_guardrails.strip_think_tags``.
# The strip logic used to be duplicated across ``ai_router``, ``chat_ai/_quality``
# and ``chat_ai/_llm``; alias here so every caller resolves to the ONE helper
# and the regexes cannot drift. Handles complete + unterminated (``max_tokens``-
# truncated) blocks; idempotent; safe on plain text.
from app.core.ai_guardrails import strip_think_tags as strip_think_blocks

# ══════════════════════════════════════════════
# GROUNDING FOOTER — DISTINCT INTELLIGENCE SOURCES
# The old footer counted only RAG hits (rag_count from X-RAG-Context), but
# answers are grounded by RAG + live web search + bot-side live tools + the
# daily market digest. Count the DISTINCT source types that ACTUALLY fed the
# answer — never fabricated, only real data that returned.
# ══════════════════════════════════════════════


def _grounding_sources_count(
    *,
    provider: str,
    rag_count: int,
    bridge_sources: list[dict[str, str]] | None = None,
    live_results: dict[str, ToolResult] | None = None,
    rag_result: ToolResult | None = None,
    has_market_context: bool = False,
) -> int:
    """Truthful count of the DISTINCT intelligence sources behind an answer.

    Buckets (each counts only real data):

    * **RAG** — the backend's real hit count (``X-RAG-Context``) on the
      ``backend`` path; on the fallback path the rag-typed bridge sources
      plus one for a direct ``rag_lookup`` hit with no typed sources.
    * **Web** — web-typed bridge sources, plus a live (non-knowledge-
      fallback) ``web_search`` tool result. The same bridge search is never
      double-counted.
    * **Live tools** — every distinct bot-side tool that returned
      ``ok=True`` (``web_search`` / ``rag_lookup`` are counted in their own
      buckets).
    * **Market** — the daily market/news digest or a bridge market block
      present in the answer context.

    Only counts what actually returned data: failed tools (``ok=False``),
    empty source lists, and knowledge-fallback web results contribute zero.
    """
    bridge_sources = bridge_sources or []
    live_results = live_results or {}

    # RAG grounding.
    if provider == "backend":
        rag_bucket = max(int(rag_count), 0)
    else:
        rag_bucket = sum(1 for s in bridge_sources if isinstance(s, dict) and s.get("type") == "rag")
        if rag_result is not None and rag_result.ok and rag_bucket == 0:
            rag_bucket = 1  # direct lookup hit without typed bridge sources
    count = rag_bucket

    # Web grounding: bridge web sources + a live web_search tool result.
    # The tool reuses the same bridge search, so it only counts when the
    # bridge did not already contribute web sources (no double count).
    web_count = sum(1 for s in bridge_sources if isinstance(s, dict) and s.get("type") == "web")
    web_tool = live_results.get("web_search")
    if (
        web_tool is not None
        and web_tool.ok
        and web_tool.source
        and not str(web_tool.source).startswith("knowledge")
        and web_count == 0
    ):
        web_count = 1
    count += web_count

    # Live tools: distinct tool types that returned real data.
    count += sum(1 for name, r in live_results.items() if name not in ("web_search", "rag_lookup") and r.ok)

    # Market context: the daily digest / bridge market block fed the answer.
    if has_market_context:
        count += 1

    return count


# ══════════════════════════════════════════════
# ANSWER POST-PROCESSING GUARDS (think-strip + degenerate-output)
# Pure, unit-testable; applied in chat_ai_response before the answer is
# stored/sent so the user and the chat history never see a reasoning chain
# or a degenerate token-repetition loop.
# ══════════════════════════════════════════════


_DEGENERATE_REPETITION_RE = re.compile(r"^(.{2,40}?)\1{2,}$", re.DOTALL)


def is_degenerate_answer(text: str) -> bool:
    """True when an LLM answer carries no usable content.

    Covers empty/whitespace, bare bullets/dashes, punctuation/emoji-only
    output, and token-repetition loops ("ioneerioneerioneer...") the local
    models degenerate into. Mirrors ``ai_router._is_degenerate`` and adds
    the repetition-loop detector — the loop carries alphanumerics, so the
    backend's punctuation-only check never catches it.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    if not any(c.isalnum() for c in stripped):
        return True
    return bool(_DEGENERATE_REPETITION_RE.match(stripped))


__all__ = [
    "_grounding_sources_count",
    "is_degenerate_answer",
    "strip_think_blocks",
]
