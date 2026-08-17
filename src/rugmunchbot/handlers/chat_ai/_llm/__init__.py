"""LLM-call clients + endpoint/budget constants + fallback orchestration.

Split from the monolithic ``_llm.py`` (2026-08-16) into focused submodules
by responsibility. This ``__init__`` is a pure re-export shim: every
public name and signature is unchanged, and callers/tests keep importing
``chat_ai._llm`` exactly as before. Patchable names (``httpx``,
``get_snappy_config``, ``get_long_config``, ``_call_remote_fast``,
``_gather_web_fallback``) stay bound here so tests that monkeypatch
``chat_ai._llm.*`` keep working (the submodules resolve them through this
package at call time).

Layout:
  * ``_constants.py`` — endpoint/budget constants + tier/question regexes
  * ``_sources.py``   — untrusted-context delimiters + source rendering
  * ``_router.py``    — tier router + question-type router + context gathers
  * ``_clients.py``   — backend RAG + remote LLM clients
  * ``_fallback.py``  — backend-less fallback orchestration
"""

from __future__ import annotations

import httpx as httpx

from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._clients import (
    _call_backend_chat as _call_backend_chat,
    _call_remote_fast as _call_remote_fast,
    _call_remote_llm as _call_remote_llm,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT as _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS as _CONTEXT_MAX_CHARS,
    _CTX_EDU_RE as _CTX_EDU_RE,
    _CTX_MARKET_BROAD_RE as _CTX_MARKET_BROAD_RE,
    _CTX_MARKET_RE as _CTX_MARKET_RE,
    _CTX_NEED_HEAVY as _CTX_NEED_HEAVY,
    _CTX_NEED_LIGHT as _CTX_NEED_LIGHT,
    _CTX_NEED_NONE as _CTX_NEED_NONE,
    _CTX_NEWS_RE as _CTX_NEWS_RE,
    _CTX_SCAM_RE as _CTX_SCAM_RE,
    _CTX_TA_RE as _CTX_TA_RE,
    _CTX_TOOL_SETS as _CTX_TOOL_SETS,
    _CTX_TRENDING_RE as _CTX_TRENDING_RE,
    _EDU_ANSWER_HINT as _EDU_ANSWER_HINT,
    _EDU_RAG_THIN_CHARS as _EDU_RAG_THIN_CHARS,
    _EDU_RAG_THIN_HITS as _EDU_RAG_THIN_HITS,
    _MARKET_ANSWER_HINT as _MARKET_ANSWER_HINT,
    _NEWS_THIN_THRESHOLD as _NEWS_THIN_THRESHOLD,
    _SNAPPY_CONTEXT_BUDGET as _SNAPPY_CONTEXT_BUDGET,
    _SNAPPY_DIGEST_CHARS as _SNAPPY_DIGEST_CHARS,
    _SNAPPY_HISTORY_CHARS as _SNAPPY_HISTORY_CHARS,
    _SNAPPY_HISTORY_MSGS as _SNAPPY_HISTORY_MSGS,
    _SNAPPY_SYSTEM_PROMPT as _SNAPPY_SYSTEM_PROMPT,
    _TA_ANSWER_HINT as _TA_ANSWER_HINT,
    _TIER_ADDRESS_OR_HASH_RE as _TIER_ADDRESS_OR_HASH_RE,
    _TIER_ANALYSIS_FORENSIC_RE as _TIER_ANALYSIS_FORENSIC_RE,
    _TIER_ANALYSIS_LENGTH_THRESHOLD as _TIER_ANALYSIS_LENGTH_THRESHOLD,
    _TIER_ANALYSIS_QUESTION_RE as _TIER_ANALYSIS_QUESTION_RE,
    _TIER_ANALYSIS_TERMS as _TIER_ANALYSIS_TERMS,
    _TIER_DEFINITIONAL_RE as _TIER_DEFINITIONAL_RE,
    _WEB_FALLBACK_TIMEOUT as _WEB_FALLBACK_TIMEOUT,
    _WEB_RESULTS_MAX as _WEB_RESULTS_MAX,
    CHAT_ENDPOINT as CHAT_ENDPOINT,
    CHAT_TIMEOUT as CHAT_TIMEOUT,
    FALLBACK_CONTEXT_HARD_CAP as FALLBACK_CONTEXT_HARD_CAP,
    FALLBACK_CONTEXT_WAIT as FALLBACK_CONTEXT_WAIT,
    MAX_MESSAGE_CHARS as MAX_MESSAGE_CHARS,
    RAG_LOOKUP_WAIT as RAG_LOOKUP_WAIT,
    REMOTE_LLM_CONTEXT_BUDGET as REMOTE_LLM_CONTEXT_BUDGET,
    REMOTE_LONG_MAX_TOKENS as REMOTE_LONG_MAX_TOKENS,
    REMOTE_LONG_TIMEOUT as REMOTE_LONG_TIMEOUT,
    REMOTE_SNAPPY_TIMEOUT as REMOTE_SNAPPY_TIMEOUT,
    REMOTE_UNAVAILABLE_MESSAGE as REMOTE_UNAVAILABLE_MESSAGE,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._fallback import (
    _build_fallback_answer as _build_fallback_answer,
    _gather_fallback_context as _gather_fallback_context,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._router import (
    _classify_context_need as _classify_context_need,
    _classify_question as _classify_question,
    _gather_context as _gather_context,
    _gather_edu_lore_context as _gather_edu_lore_context,
    _gather_news_context as _gather_news_context,
    _gather_rag_news_context as _gather_rag_news_context,
    _gather_web_fallback as _gather_web_fallback,
    _render_web_results as _render_web_results,
    _route_tier as _route_tier,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._sources import (
    _MD_LINK_RE as _MD_LINK_RE,
    _PRIMARY_SOURCE_MARKERS as _PRIMARY_SOURCE_MARKERS,
    _append_source_block as _append_source_block,
    _dedupe_sources as _dedupe_sources,
    _internal_source_label as _internal_source_label,
    _links_to_plain_text as _links_to_plain_text,
    _normalize_source_domain as _normalize_source_domain,
    _scrub_internal_api_refs as _scrub_internal_api_refs,
    _source_display_name as _source_display_name,
    _source_is_ours as _source_is_ours,
    _source_is_primary as _source_is_primary,
    _source_type_label as _source_type_label,
    _untrusted_block as _untrusted_block,
)
from app.domains.telegram.rugmunchbot.prompts import (
    FALLBACK_SYSTEM_PROMPT as FALLBACK_SYSTEM_PROMPT,
    PERSONA_CONTEXT as PERSONA_CONTEXT,
)
from app.llm_config import get_long_config as get_long_config, get_snappy_config as get_snappy_config
