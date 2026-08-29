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
import re
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.tools.story_fetch")

# Full clean text returned to the caller is capped so a huge page cannot blow
# the chat prompt. Longer articles stay intact in RAG when ingest=true.
STORY_TEXT_MAX = 20_000
STORY_TEXT_PREVIEW = 1_200
STORY_FETCH_TIMEOUT = 12.0  # must stay under the registry timeout (14s)
STORY_INGEST_TIMEOUT = 10.0

#: X post URL carrying a numeric status id (x.com / twitter.com, incl. /i/status).
_X_STATUS_RE = re.compile(r"https?://(?:x\.com|twitter\.com)/(?:[^/]+)/status/(\d+)", re.IGNORECASE)
#: ANY x.com / twitter.com URL (post, profile, timeline — all X pages).
_X_DOMAIN_RE = re.compile(r"https?://(?:x\.com|twitter\.com)/", re.IGNORECASE)

#: Generic refusal when an X page is handed to the story crawler.
_X_SCRAPE_REFUSED = (
    "X posts are read via ApiPass only — direct scraping of X is disabled. "
    "Use /tweet <url> or /tweets <@handle>."
)


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

    # ── X-post guard (WIRE X via ApiPass — NEVER direct scrape) ─────────────
    # An x.com/twitter.com URL must NOT reach the generic crawler
    # (crawl4ai / FlareSolverr / pryscraper / httpx). X post payloads come
    # exclusively from tools.apipass_x; everything else fails open with a
    # clean note (the chat answer then says it can't read X, never guesses).
    x_post = _X_STATUS_RE.search(url)
    if x_post or _X_DOMAIN_RE.match(url):
        if not x_post:
            # Profile / timeline / non-post X page — no tweet payload to read.
            return ToolResult(
                tool="story_fetch",
                ok=False,
                data={"url": url, "source": "apipass_x"},
                error=_X_SCRAPE_REFUSED,
            )
        try:
            from rugmunchbot.tools.apipass_x import api_key_configured, fetch_tweet_detail

            if not api_key_configured():
                return ToolResult(
                    tool="story_fetch",
                    ok=False,
                    data={"url": url, "source": "apipass_x"},
                    error=(
                        "X posts are read via ApiPass only and no ApiPass key is "
                        "configured — direct scraping of X is disabled (operator: "
                        "see docs/TROUBLESHOOTING.md → ApiPass)."
                    ),
                )
            tweet = await fetch_tweet_detail(x_post.group(1))
            if not tweet:
                return ToolResult(
                    tool="story_fetch",
                    ok=False,
                    data={"url": url, "source": "apipass_x"},
                    error=(
                        "ApiPass could not fetch that X post (model not provisioned "
                        "or task failed) — direct scraping of X is disabled."
                    ),
                )
            text = str(tweet.get("text") or "")
            data: dict[str, Any] = {
                "url": tweet.get("url") or url,
                "title": f"@{tweet.get('handle') or 'unknown'} on X",
                "author": tweet.get("author_name") or tweet.get("handle") or "",
                "published_at": tweet.get("created_at") or "",
                "source": "apipass_x",
                "fetch_chain": ["apipass_x"],
                "text": text[:STORY_TEXT_MAX],
                "text_preview": text[:STORY_TEXT_PREVIEW],
                "chars": len(text),
                "truncated": len(text) > STORY_TEXT_MAX,
                "tweet": tweet,
            }
            return ToolResult(
                tool="story_fetch",
                ok=True,
                data=data,
                symbols=[],
                addresses=[],
                source="story_fetch:apipass_x",
            )
        except Exception as exc:  # fail-open by contract — never let X hit the crawler
            logger.info("story_fetch_x_apipass_failed", url=url, error=str(exc)[:160])
            return ToolResult(
                tool="story_fetch",
                ok=False,
                data={"url": url, "source": "apipass_x"},
                error=_X_SCRAPE_REFUSED,
            )

    try:
        from rugmunchbot._vendor.rag_pkg.story_ingest import fetch_story
    except Exception as e:  # pragma: no cover - import must never raise out
        logger.error("story_fetch_import_failed", error=str(e)[:200])
        return ToolResult(
            tool="story_fetch", ok=False, error="couldn't fetch: story fetch layer unavailable"
        )

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
            from rugmunchbot._vendor.rag_pkg.story_ingest import ingest_url

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
