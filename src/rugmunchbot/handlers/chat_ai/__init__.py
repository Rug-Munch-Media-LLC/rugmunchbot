#!/usr/bin/env python3
"""RugMunchBot -- SINGLE free-text AI chat pipeline (merged, v5+).

This package is the ONE pipeline for every free-text message plus the
/chat and /ask commands. It was created by merging the old
``handlers/message.py`` free-text pipeline into the backend RAG-wired chat
handler so no feature is shadowed by a duplicate registration.

Since the Batch 12 de-godding pass this package is a THIN RE-EXPORT SHIM —
all logic lives in sibling modules:

  * ``_routing.py``  — chat routing: owner admin console, structural feature
    routing, the shared AI core (``chat_ai_response``), answer delivery and
    ``register_chat_ai`` wiring.
  * ``_live_tools.py`` — bot-side live tool dispatch + timing budgets.
  * ``_guards.py``   — entry guards, answer-quality guardrails, fallback replies.
  * ``_llm.py``      — LLM clients + the backend-less fallback orchestration.
  * ``_helpers.py``  — pure helpers + message constants (itself split into
    ``_markup.py`` / ``_quality.py`` / ``_classify.py`` / ``_local_ai.py``).
  * ``_format.py`` / ``_parts.py`` / ``_trace.py`` — formatting / chunking / tracing.

Every public name and signature is unchanged — ``from
app.domains.telegram.rugmunchbot.handlers.chat_ai import chat_ai_response``
and the ``chat_ai._llm`` test imports keep working exactly as before.
Imports not listed in ``__all__`` are re-exported (redundant aliases) because
the pipeline tests patch them on this package at runtime — e.g.
``chat_ai.check_flood``, ``chat_ai._call_backend_chat``,
``chat_ai.httpx.AsyncClient``.

Pipeline order (free text, ``ai_chat_handler``):
  1. guards (user / banned / spam) + ``record_activity(msg=True)``
  2. owner admin console (private DMs, runs before the kill-switch)
  3. bot kill-switch
  4. ``track_message()`` analytics
  5. prompt-injection block
  6. structural routing (NO AI quota): known-scam block -> bare address
     /scan -> ``$TOKEN`` price card
  7. ``chat_ai_response`` (shared AI core): chat_enabled gate -> injection ->
     weekly quota -> flood -> gibberish (tier-2/3) -> backend RAG chat
     (tier-1) -> remote LLM fallback

Import-time side effects preserved: ``SCAN_ENDPOINT`` is still built from
``BACKEND_URL`` at import time (in ``_routing.py``, imported eagerly here)
and ``cmd_scan`` stays lazily imported from ``handlers/scanning.py`` inside
``_route_structural``.
"""

from __future__ import annotations

import httpx as httpx

from app.domains.telegram.rugmunchbot import anti_abuse as anti_abuse
from app.domains.telegram.rugmunchbot.flood_control import check_flood as check_flood, refund_flood as refund_flood
from app.domains.telegram.rugmunchbot.formatters import is_evm as is_evm, is_sol as is_sol, thin_sep
from app.domains.telegram.rugmunchbot.handlers.chat_ai._format import (
    _format_tool_answer as _format_tool_answer,
    _render_bridge_actions as _render_bridge_actions,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._helpers import (
    CHUNK_MAX_CHARS,
    MAX_ADDRESS_HINTS,
    MAX_ANSWER_CHARS,
    _beautify_answer as _beautify_answer,
    _cap_answer as _cap_answer,
    _extract_addresses as _extract_addresses,
    _format_scan_summary as _format_scan_summary,
    _grounding_sources_count as _grounding_sources_count,
    _is_capability_question as _is_capability_question,
    _is_deep_question as _is_deep_question,
    _is_greeting_or_thanks as _is_greeting_or_thanks,
    _is_meaningful_question as _is_meaningful_question,
    _is_section_header,
    _markdown_bold_to_html as _markdown_bold_to_html,
    _markdown_to_html as _markdown_to_html,
    _normalize_llm_markup as _normalize_llm_markup,
    _tables_to_lists as _tables_to_lists,
    is_degenerate_answer,
    local_ai_response,
    strip_think_blocks,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._live_tools import (
    LIVE_TOOLS_WAIT as LIVE_TOOLS_WAIT,
    TOOL_ANSWER_EXTRA_WAIT as TOOL_ANSWER_EXTRA_WAIT,
    _run_live_tools as _run_live_tools,
    _run_live_tools_bounded as _run_live_tools_bounded,
    _run_rag_lookup as _run_rag_lookup,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm import (
    CHAT_ENDPOINT,  # re-exported (public __all__ / tests)
    CHAT_TIMEOUT,  # re-exported (public __all__)
    _call_backend_chat as _call_backend_chat,  # re-exported (tests import from package)
    _call_remote_llm as _call_remote_llm,  # monkeypatch seam (tests patch on package)
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._parts import send_in_parts, split_in_parts
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing import (
    BRIDGE_CONTEXT_ENDPOINT as BRIDGE_CONTEXT_ENDPOINT,
    BRIDGE_TIMEOUT as BRIDGE_TIMEOUT,
    MAX_MESSAGE_CHARS as MAX_MESSAGE_CHARS,
    SCAN_ENDPOINT,
    SCAN_TIMEOUT,
    _lookup_token_symbol as _lookup_token_symbol,
    _route_structural as _route_structural,
    _send_answer as _send_answer,
    ai_chat_handler,
    chat_ai_response,
    register_chat_ai,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._trace import trace_answer as trace_answer
from app.domains.telegram.rugmunchbot.states import check_spam as check_spam
from app.domains.telegram.rugmunchbot.tools.guardrails import NO_LIVE_DATA_MESSAGE as NO_LIVE_DATA_MESSAGE

__all__ = [
    "CHAT_ENDPOINT",
    "CHAT_TIMEOUT",
    "CHUNK_MAX_CHARS",
    "MAX_ADDRESS_HINTS",
    "MAX_ANSWER_CHARS",
    "SCAN_ENDPOINT",
    "SCAN_TIMEOUT",
    "_is_section_header",
    "ai_chat_handler",
    "chat_ai_response",
    "is_degenerate_answer",
    "local_ai_response",
    "register_chat_ai",
    "send_in_parts",
    "split_in_parts",
    "strip_think_blocks",
    "thin_sep",
]
