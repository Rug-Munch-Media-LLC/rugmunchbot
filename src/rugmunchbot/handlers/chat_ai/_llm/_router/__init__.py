"""LLM tier router + question-type router + context gathers (package).

Split from the former monolithic ``_router.py`` into focused submodules.
This ``__init__`` is a pure re-export shim: every public name and signature
is unchanged, and callers keep importing
``chat_ai._llm._router`` exactly as before.

Layout:
  * ``_routing.py``  — deterministic tier router + question-type/cost classifiers
  * ``_web.py``      — SearXNG web fallback (patchable via ``chat_ai._llm._gather_web_fallback``)
  * ``_news.py``     — RAG news_v2 + concurrent live web layer
  * ``_trending.py`` — ~20-token trending block + narrative drivers
  * ``_market.py``   — market-today rich context + shared narrative fetch
  * ``_edu.py``      — education/lore RAG KB gather
  * ``_gather.py``   — cost-gated context-gathering entry point
"""

from __future__ import annotations

from rugmunchbot.handlers.chat_ai._llm._router._edu import (
    _gather_edu_lore_context as _gather_edu_lore_context,
)
from rugmunchbot.handlers.chat_ai._llm._router._gather import (
    _gather_context as _gather_context,
)
from rugmunchbot.handlers.chat_ai._llm._router._market import (
    _build_market_today_block as _build_market_today_block,
    _extract_majors as _extract_majors,
    _fetch_market_narrative as _fetch_market_narrative,
    _gather_market_today_context as _gather_market_today_context,
    _mover_driver as _mover_driver,
    _mover_label as _mover_label,
    _movers_from_trending as _movers_from_trending,
    _what_to_watch_seed as _what_to_watch_seed,
)
from rugmunchbot.handlers.chat_ai._llm._router._news import (
    _gather_news_context as _gather_news_context,
    _gather_rag_news_context as _gather_rag_news_context,
)
from rugmunchbot.handlers.chat_ai._llm._router._routing import (
    _TRENDING_COUNT_RE as _TRENDING_COUNT_RE,
    _classify_context_need as _classify_context_need,
    _classify_question as _classify_question,
    _requested_token_count as _requested_token_count,
    _route_tier as _route_tier,
)
from rugmunchbot.handlers.chat_ai._llm._router._trending import (
    _build_trending_context_block as _build_trending_context_block,
    _gather_trending_context as _gather_trending_context,
    _render_trending_section as _render_trending_section,
    _render_trending_tokens as _render_trending_tokens,
)
from rugmunchbot.handlers.chat_ai._llm._router._web import (
    _gather_web_fallback as _gather_web_fallback,
    _render_web_results as _render_web_results,
)
