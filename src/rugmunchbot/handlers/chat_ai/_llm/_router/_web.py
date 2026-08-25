"""SearXNG web fallback for thin/empty router sources (fail-open).

``_gather_web_fallback`` is a PATCHABLE name: tests may monkeypatch
``chat_ai._llm._gather_web_fallback``, so callers resolve it through the
``_llm`` package at call time (``_llm_mod._gather_web_fallback``) instead of
importing it directly.
"""

from __future__ import annotations

import asyncio
import html

from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_MAX_CHARS,
    _WEB_FALLBACK_TIMEOUT,
    _WEB_RESULTS_MAX,
)
from rugmunchbot.handlers.chat_ai._llm._sources import _normalize_source_domain, _source_is_junk
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.chat_ai")


def _render_web_results(results: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Render SearXNG results as an HTML-escaped context block + sources.

    Web snippets/pages are UNTRUSTED data — a page could embed markup that
    tries to inject HTML into the final Telegram answer, so every field
    (snippet, title, URL) is ``html.escape``d here; the caller wraps the
    block in the ``<untrusted_context>`` delimiter and the sources flow into
    the grounding footer (``_append_source_block`` renders them as plain
    ``title — url`` text; ``_send_answer``'s single ``html.escape`` is the
    escape layer). Returns ``("", [])`` when nothing usable survived.
    """
    lines = ["LIVE WEB SEARCH (real-time web results — cite by source/URL):"]
    sources: list[dict[str, str]] = []
    seen_domains: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        title = str(r.get("title") or "").strip()
        snippet = str(r.get("snippet") or "").strip()
        if not url:
            continue
        # Junk-domain gate (2026-08-25): dictionaries, find-my-phone pages,
        # content farms etc. never enter the context OR the sources footer —
        # a real URL can still be a worthless citation.
        if _source_is_junk(url):
            continue
        # One page per domain — both the context block the model reads and
        # the sources footer stay clean (first result per domain wins).
        domain = _normalize_source_domain(url)
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        text = (snippet or title).strip()
        if not text:
            continue
        label = (title or url)[:60]
        lines.append(
            f"- {html.escape(text[:200])} (source: {html.escape(label)} — {html.escape(url)})"
        )
        sources.append({"type": "web", "title": text[:60], "url": url})
    if len(lines) == 1:
        return "", []
    return "\n".join(lines)[:_CONTEXT_MAX_CHARS], sources


async def _gather_web_fallback(question: str) -> tuple[str, list[dict[str, str]]]:
    """SearXNG web fallback for thin/empty router sources (fail-open).

    Uses ``tools.web.searxng_top_results`` (SearXNG-only — no bridge, no
    knowledge LLM), bounded by ``_WEB_FALLBACK_TIMEOUT`` so the router stays
    close to its ~5s snappy budget. Any failure returns empty context and
    the LLM answers from its own knowledge.
    """
    try:
        from rugmunchbot.tools.web import searxng_top_results

        results = await asyncio.wait_for(
            searxng_top_results(question, limit=_WEB_RESULTS_MAX),
            timeout=_WEB_FALLBACK_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("question-router web fallback failed (query %.60s): %s", question, exc)
        return "", []
    if not results:
        return "", []
    return _render_web_results(results)
