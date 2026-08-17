"""story_fetch tool — pull ANY story from ANY site into the chat loop.

Layered fetch (see ``app.rag.story_ingest`` for the shared chain):

  crawl4ai (configured primary) → FlareSolverr (Cloudflare) → pryscraper
  → plain httpx (proxy rotator egress + stdlib HTML→text)

The tool returns REAL fetched content only — clean text, title, author and
publish date when the page exposes them. Zero fabrication: on total failure
it returns ``ok=False`` with a "couldn't fetch" error and the caller must
say it couldn't retrieve the page instead of guessing.

Bounded + non-blocking: the registry wraps the handler in a timeout
(``story_fetch`` is registered with a 14s cap) and every layer has its own
internal timeout, so a dead crawler or a slow site never blocks the loop.

Optional RAG side-effect: pass ``ingest=true`` to also push the story
through ``app.rag.story_ingest.ingest_url`` (chunk + embed with source
attribution). The ingest is fail-open — a RAG outage never drops the story
that was already fetched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.tools.story_fetch")

# Full clean text returned to the caller is capped so a huge page cannot blow
# the chat prompt. Longer articles stay intact in RAG when ingest=true.
STORY_TEXT_MAX = 20_000
STORY_TEXT_PREVIEW = 1_200
STORY_FETCH_TIMEOUT = 12.0  # must stay under the registry timeout (14s)
STORY_INGEST_TIMEOUT = 10.0


async def story_fetch(*, url: str = "", ingest: bool = False, **ctx: Any) -> ToolResult:
    """Fetch a story URL → clean markdown/text + title + author + publish date.

    Args:
        url: The story URL (http/https only).
        ingest: When True, also ingest the fetched story into RAG (news_v2)
            with source attribution (fail-open side-effect).
        **ctx: Ignored shared context (history, chat ids, ...).

    Returns:
        ``ToolResult`` — on success ``data`` carries ``url``, ``title``,
        ``author``, ``published_at``, ``source``, ``fetch_chain``, ``text``
        (capped at ``STORY_TEXT_MAX`` chars), ``text_preview``, ``chars``,
        and (when ``ingest``) ``ingest`` = {doc_id, chunks, status, ...}.
        On failure ``ok=False`` with ``error="couldn't fetch: ..."``.
    """
    if not url:
        return ToolResult(tool="story_fetch", ok=False, error="url required")

    try:
        from app.rag.story_ingest import fetch_story
    except Exception as e:  # pragma: no cover - import must never raise out
        logger.error("story_fetch_import_failed", error=str(e)[:200])
        return ToolResult(tool="story_fetch", ok=False, error="couldn't fetch: story fetch layer unavailable")

    story = await fetch_story(url, timeout=STORY_FETCH_TIMEOUT)
    if not story.ok:
        logger.info("story_fetch_failed", url=url, chain=story.chain, error=story.error)
        return ToolResult(
            tool="story_fetch",
            ok=False,
            data={"url": url, "fetch_chain": story.chain},
            error=story.error or "couldn't fetch",
            source=story.source or "story_fetch",
        )

    text = story.text[:STORY_TEXT_MAX]
    data: dict[str, Any] = {
        "url": story.url,
        "title": story.title,
        "author": story.author,
        "published_at": story.published_at,
        "source": story.source,
        "fetch_chain": story.chain,
        "text": text,
        "text_preview": text[:STORY_TEXT_PREVIEW],
        "chars": len(text),
        "truncated": len(story.text) > STORY_TEXT_MAX,
    }

    # Optional RAG side-effect — fail-open, never blocks the story return.
    if ingest:
        try:
            from app.rag.story_ingest import ingest_url

            result = await asyncio.wait_for(ingest_url(url), timeout=STORY_INGEST_TIMEOUT)
            data["ingest"] = result if isinstance(result, dict) else {"status": "unknown"}
        except TimeoutError:
            logger.warning("story_fetch_ingest_timeout", url=url)
            data["ingest"] = {"status": "failed", "error": "timeout"}
        except Exception as e:
            logger.warning("story_fetch_ingest_failed", url=url, error=str(e)[:200])
            data["ingest"] = {"status": "failed", "error": str(e)[:200]}

    return ToolResult(
        tool="story_fetch",
        ok=True,
        data=data,
        symbols=[],
        addresses=[],
        source=f"story_fetch:{story.source}",
    )


__all__ = ["story_fetch"]
