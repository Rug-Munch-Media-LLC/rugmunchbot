"""web_search tool — REAL web/news context via SearXNG + the Chat RAG Bridge.

Layers (free stack, per AGENTS.md):
  1. Chat RAG Bridge (http://127.0.0.1:8095/chat/context) — live SearXNG
     web search + RAG knowledge + market digest + scanner context in one
     call (fail-open). This is the same bridge the chat pipeline already
     uses.
  2. SearXNG direct (SEARXNG_URL env, default http://127.0.0.1:8889) with
     JSON output.
  3. FlareSolverr (http://127.0.0.1:8191) — TODO: only for Cloudflare-
     blocked pages; not wired until a blocked source is confirmed.

Web results are grounding context — the LLM cites them, it never invents.
"""

from __future__ import annotations

import os

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json, http_post_json
from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.tools.web")

BRIDGE_CONTEXT_ENDPOINT = os.getenv("BRIDGE_CONTEXT_ENDPOINT", "http://127.0.0.1:8095/chat/context")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8889").rstrip("/")
SEARXNG_TIMEOUT = 8.0


async def _searxng_search(query: str, limit: int = 5) -> list[dict[str, str]]:
    body = await http_get_json(
        f"{SEARXNG_URL}/search",
        params={"q": query, "format": "json"},
        timeout=SEARXNG_TIMEOUT,
    )
    results = body.get("results") if body else None
    if not isinstance(results, list):
        return []
    out: list[dict[str, str]] = []
    for r in results[:limit]:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "").strip()
        url = str(r.get("url") or "").strip()
        snippet = str(r.get("content") or "").strip()
        if title and url:
            out.append({"title": title, "url": url, "snippet": snippet[:300]})
    return out


async def _bridge_context(
    query: str, history: list[dict[str, str]] | None = None
) -> tuple[str, list[dict[str, str]]]:
    body = await http_post_json(
        BRIDGE_CONTEXT_ENDPOINT,
        json={"message": query[:8000], "history": history or []},
        timeout=12.0,
    )
    if not body:
        return "", []
    raw_blocks = body.get("blocks")
    blocks = raw_blocks if isinstance(raw_blocks, dict) else {}
    parts: list[str] = []
    for key in (
        "web_context",
        "rag_context",
        "market_context",
        "scanner_context",
        "rundown_context",
    ):
        val = (blocks.get(key) or "").strip()
        if val:
            parts.append(val)
    raw_sources = body.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    sources = [s for s in sources if isinstance(s, dict)]
    return "\n\n".join(parts), sources


async def web_search(*, query: str = "", history: list[dict[str, str]] | None = None) -> ToolResult:
    """Live web/news context for a query. Returns structured results."""
    if not query:
        return ToolResult(tool="web_search", ok=False, error="query required")

    # Layer 1: bridge (SearXNG + RAG + market digest in one call).
    bridge_text, sources = await _bridge_context(query, history)
    if bridge_text:
        return ToolResult(
            tool="web_search",
            ok=True,
            data={"query": query, "context": bridge_text, "sources": sources},
            symbols=[],
            addresses=[],
            source="bridge:/chat/context",
        )

    # Layer 2: SearXNG direct.
    results = await _searxng_search(query)
    if results:
        return ToolResult(
            tool="web_search",
            ok=True,
            data={"query": query, "results": results},
            symbols=[],
            addresses=[],
            source="searxng",
        )

    return ToolResult(
        tool="web_search", ok=False, error="no live web results (bridge + SearXNG unavailable)"
    )
