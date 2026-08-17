# VENDORED 2026-08-17 from rmi-backend/app/rag/story_ingest.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Story fetch + ingest glue — "pull any story from any site" into RAG.

This module owns the shared story-fetch chain used by:

  * the RugMunchBot ``story_fetch`` tool (URL → clean text + title + author
    + publish date), and
  * the RAG news-ingestion helper ``ingest_url`` (fetch → chunk → embed →
    Qdrant via the existing ``app/rag`` pipeline, with source attribution).

Fetch chain (configured primary first, working fallback second — each layer
is lazy-imported and fail-open so a dead layer never blocks the next):

  1. ``crawl4ai``        — JS-aware headless browser; the configured primary
                          (``app.rag.crawler.crawl_crypto_page``). Skipped
                          with a log line when crawl4ai is not installed.
  2. ``FlareSolverr``    — Cloudflare bypass (``POST /v1`` request.get).
  3. ``pryscraper``      — self-hosted scraping service (PryscraperClient).
  4. ``plain``           — direct httpx GET through the residential proxy
                          rotator; HTML → text via the stdlib
                          ``clean_article_html`` helper (trafilatura is used
                          when present, else the stdlib HTMLParser path).

Every outbound hop routes through ``app.databus.proxy_rotator`` for egress
when it is enabled. The chain is bounded (per-layer + overall timeouts) and
sequential — one source first, fallback only on a miss.

Zero fabrication: only content that was actually fetched is returned; on
total failure the caller receives ``StoryFetch.ok == False`` with a
"couldn't fetch" error, never invented text.
"""

from __future__ import annotations

import asyncio
import contextlib
import html as html_module
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from rugmunchbot._vendor.logging_shim import get_logger

log = get_logger(__name__)

# ── Configuration (env-overridable, read once per process) ────────────────
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8191").rstrip("/")
FLARESOLVERR_TIMEOUT = 8.0
PLAIN_TIMEOUT = 10.0
PRYSCRAPER_TIMEOUT = 12.0
CRAWL4AI_TIMEOUT = 12.0  # outer budget for crawl_crypto_page (it has its own page timeout)
MAX_URL_LENGTH = 2048
MAX_PROXY_ATTEMPTS = 2  # proxy retries before falling back to direct

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"


class StoryFetchError(RuntimeError):
    """Raised when every fetch layer failed. Message is LLM-safe ("couldn't fetch")."""


@dataclass
class StoryFetch:
    """Fetched story content (only ever REAL fetched content)."""

    url: str
    text: str = ""
    title: str = ""
    author: str = ""
    published_at: str = ""
    source: str = ""  # fetch layer that produced the content
    chain: list[str] = field(default_factory=list)  # layers attempted, in order
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


# ── URL validation ─────────────────────────────────────────────────────────


def _validate_url(url: str) -> str:
    """Reject non-http(s) URLs, oversized URLs, and embedded credentials."""
    if not url or len(url) > MAX_URL_LENGTH:
        raise StoryFetchError("invalid URL")
    try:
        parts = urlparse(url)
    except ValueError as e:
        raise StoryFetchError("invalid URL") from e
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise StoryFetchError("unsupported URL — only http/https allowed")
    if "@" in parts.netloc:
        raise StoryFetchError("URLs with embedded credentials are not allowed")
    return url


# ── Best-effort metadata extraction (stdlib only, never fatal) ────────────

_META_TAG_RE = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR_NAME_RE = re.compile(r'name=["\']([^"\']+)["\']', re.I)
_ATTR_PROPERTY_RE = re.compile(r'property=["\']([^"\']+)["\']', re.I)
_ATTR_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.I | re.S)
_AUTHOR_KEYS = {"author", "article:author", "byl", "parsely-author", "dc.creator", "sailthru.author"}
_DATE_KEYS = {
    "date",
    "dc.date",
    "parsely-pub-date",
    "sailthru.date",
    "article:published_time",
    "article:modified_time",
    "og:published_time",
    "og:updated_time",
}
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?)")


def _clean_meta_value(raw: str) -> str:
    value = html_module.unescape(raw or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value[:500]


def _first_date(value: str) -> str:
    m = _DATE_RE.search(value)
    return m.group(1) if m else value[:64]


def _extract_meta_from_html(html_text: str, url: str) -> tuple[str, str, str]:
    """Best-effort (title, author, published_at) from raw HTML. Stdlib only."""
    title = ""
    m = _TITLE_RE.search(html_text)
    if m:
        title = _clean_meta_value(m.group(1))
    author = ""
    published = ""
    for tag in _META_TAG_RE.findall(html_text):
        name = ""
        prop = ""
        content = ""
        nm = _ATTR_NAME_RE.search(tag)
        pm = _ATTR_PROPERTY_RE.search(tag)
        cm = _ATTR_CONTENT_RE.search(tag)
        if nm:
            name = nm.group(1).lower()
        if pm:
            prop = pm.group(1).lower()
        if cm:
            content = _clean_meta_value(cm.group(1))
        if not content:
            continue
        if name in _AUTHOR_KEYS or prop in ("article:author",):
            if not author:
                author = content
        elif (name in _DATE_KEYS or prop in _DATE_KEYS) and not published:
            published = content
    if not title:
        m2 = _OG_TITLE_RE.search(html_text)
        if m2:
            title = _clean_meta_value(m2.group(1))
    if not title:
        # Fall back to a bare hostname label — never fabricate a headline.
        try:
            title = (urlparse(url).hostname or "").removeprefix("www.")[:200]
        except ValueError:
            title = ""
    return title, author, published


# ── Fetch layers (each returns (text, title, author, published) | None) ────


async def _fetch_crawl4ai(url: str) -> tuple[str, str, str, str] | None:
    """Layer 1: crawl4ai — the configured primary (skipped when not installed)."""
    try:
        from rugmunchbot._vendor.rag_pkg.crawler import crawl_crypto_page
    except Exception as e:  # ImportError or module-level breakage — degrade
        log.info("story_fetch_crawl4ai_unavailable", error=str(e)[:120])
        return None
    try:
        page = await asyncio.wait_for(crawl_crypto_page(url), timeout=CRAWL4AI_TIMEOUT)
        text = str(page.get("content") or "").strip()
        if not text:
            log.info("story_fetch_crawl4ai_empty", url=url)
            return None
        meta = page.get("metadata") or {}
        title = str(page.get("title") or meta.get("title") or "")
        author = str(meta.get("author") or "")
        published = str(meta.get("date") or meta.get("published_at") or "")
        return text, title, author, _first_date(published)
    except TimeoutError:
        log.warning("story_fetch_crawl4ai_timeout", url=url)
        return None
    except Exception as e:
        log.warning("story_fetch_crawl4ai_failed", url=url, error=str(e)[:200])
        return None


async def _fetch_flaresolverr(url: str, rotator: Any) -> tuple[str, str, str, str] | None:
    """Layer 2: FlareSolverr Cloudflare bypass (proxy rotator when enabled)."""
    import httpx

    proxy = rotator.get_rotating_proxy() if rotator else None
    payload: dict[str, Any] = {"cmd": "request.get", "url": url, "maxTimeout": 20_000}
    if proxy:
        payload["proxy"] = proxy
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(FLARESOLVERR_TIMEOUT, connect=3.0)) as client:
            resp = await client.post(f"{FLARESOLVERR_URL}/v1", json=payload)
            if resp.status_code != 200:
                if rotator:
                    rotator.mark_failed(proxy)
                log.info("story_fetch_flaresolverr_http", url=url, status=resp.status_code)
                return None
            body = resp.json()
        solution = body.get("solution") or {}
        html_text = str(solution.get("response") or "")
        if not html_text.strip():
            return None
        from rugmunchbot._vendor.rag_pkg._crawler_helpers import clean_article_html

        text = clean_article_html(html_text).strip()
        if not text:
            return None
        title, author, published = _extract_meta_from_html(html_text, url)
        return text, title, author, _first_date(published)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as e:
        log.warning("story_fetch_flaresolverr_failed", url=url, error=str(e)[:200])
        return None


async def _fetch_pryscraper(url: str) -> tuple[str, str, str, str] | None:
    """Layer 3: pryscraper (self-hosted, x402-capable)."""
    try:
        from rugmunchbot._vendor.pryscraper_client import PryscraperClient
    except Exception as e:
        log.info("story_fetch_pryscraper_unavailable", error=str(e)[:120])
        return None
    client = PryscraperClient()
    try:
        result = await asyncio.wait_for(
            client.scrape(url, formats=["markdown"], js_render=False), timeout=PRYSCRAPER_TIMEOUT
        )
        if not result.success:
            return None
        data = result.data if isinstance(result.data, dict) else {}
        markdown = data.get("markdown") or data.get("content") or data.get("text") or ""
        text = str(markdown).strip()
        if not text:
            log.info("story_fetch_pryscraper_empty", url=url)
            return None
        title = str(data.get("title") or "")
        author = str(data.get("author") or "")
        published = str(data.get("published_at") or data.get("date") or "")
        return text, title, author, _first_date(published)
    except TimeoutError:
        log.warning("story_fetch_pryscraper_timeout", url=url)
        return None
    except Exception as e:
        log.warning("story_fetch_pryscraper_failed", url=url, error=str(e)[:200])
        return None
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()


def _plain_client(timeout: float = PLAIN_TIMEOUT) -> Any:
    """Plain httpx client (used when the proxy rotator is disabled)."""
    import httpx

    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=5.0, read=timeout, write=8.0),
        follow_redirects=True,
    )


async def _plain_get_once(client: Any, url: str) -> Any:
    import httpx

    try:
        return await client.get(url, headers={"User-Agent": _USER_AGENT})
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError):
        return None


async def _fetch_plain(url: str, rotator: Any) -> tuple[str, str, str, str] | None:
    """Layer 4: plain GET via the proxy rotator → stdlib HTML→text.

    Retries through up to MAX_PROXY_ATTEMPTS rotating proxies on 403/429 or
    transport errors, then falls back to a direct request (rotator returns
    None when all proxies are down — same degrade path).
    """
    from rugmunchbot._vendor.rag_pkg._crawler_helpers import clean_article_html

    attempts = 0
    proxy: str | None = rotator.get_rotating_proxy() if rotator else None
    while True:
        client = rotator.get_client(proxy=proxy) if rotator else _plain_client()
        client.headers.update({"User-Agent": _USER_AGENT})
        try:
            async with client:
                resp = await _plain_get_once(client, url)
        except Exception:
            log.debug("story_fetch_plain_transport_error url=%s proxy=%s", url, proxy, exc_info=True)
            resp = None
        if resp is None or resp.status_code in (403, 429):
            # Transport error or Cloudflare/rate-limit: rotate and retry.
            if rotator and attempts < MAX_PROXY_ATTEMPTS:
                proxy = rotator.next_after_failure(proxy)
                attempts += 1
                continue
            if resp is not None:
                log.info("story_fetch_plain_blocked", url=url, status=resp.status_code)
            return None
        if resp.status_code != 200:
            return None
        html_text = resp.text
        text = clean_article_html(html_text).strip()
        if not text:
            return None
        title, author, published = _extract_meta_from_html(html_text, url)
        return text, title, author, _first_date(published)


# ── Public fetch chain ─────────────────────────────────────────────────────


async def fetch_story(url: str, *, timeout: float = 12.0, rotator: Any = None) -> StoryFetch:
    """Fetch a story from any URL through the layered chain.

    Returns ``StoryFetch`` with ``text``/``title``/``author``/``published_at``
    from the FIRST layer that produced non-empty content. Never raises for
    fetch failures — on total failure ``StoryFetch.ok`` is False with
    ``error="couldn't fetch: ..."``.

    Args:
        url: The story URL (http/https only).
        timeout: Total budget for the whole chain (seconds). Each layer gets
            the remaining budget, so a dead layer cannot starve the next one.
        rotator: Optional proxy rotator instance (defaults to the process
            singleton from ``app.databus.proxy_rotator``).
    """
    try:
        url = _validate_url(url)
    except StoryFetchError as e:
        return StoryFetch(url=url, error=str(e))

    if rotator is None:
        try:
            from rugmunchbot._vendor.proxy_rotator import get_proxy_rotator

            rotator = get_proxy_rotator()
        except Exception as e:  # pragma: no cover - defensive degrade to direct
            log.info("story_fetch_rotator_unavailable", error=str(e)[:120])
            rotator = None

    # Factories (not coroutines) so a layer that wins early never creates
    # un-awaited coroutines for the layers behind it.
    layers: list[tuple[str, Any]] = [
        ("crawl4ai", lambda: _fetch_crawl4ai(url)),
        ("flaresolverr", lambda: _fetch_flaresolverr(url, rotator)),
        ("pryscraper", lambda: _fetch_pryscraper(url)),
        ("plain", lambda: _fetch_plain(url, rotator)),
    ]
    chain: list[str] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    for name, factory in layers:
        chain.append(name)
        remaining = max(0.5, deadline - loop.time())
        try:
            coro = factory()
            result = await asyncio.wait_for(coro, timeout=remaining)
        except TimeoutError:
            log.warning("story_fetch_layer_timeout", layer=name, url=url)
            result = None
        except Exception as e:  # pragma: no cover - defensive; layers catch their own
            log.warning("story_fetch_layer_error", layer=name, url=url, error=str(e)[:200])
            result = None
        if result is None:
            continue
        text, title, author, published = result
        if text:
            return StoryFetch(
                url=url,
                text=text,
                title=title or "",
                author=author or "",
                published_at=published or "",
                source=name,
                chain=chain,
            )

    return StoryFetch(url=url, chain=chain, error="couldn't fetch: all fetch layers failed")


# ── RAG ingestion glue ─────────────────────────────────────────────────────


async def ingest_url(
    url: str,
    *,
    collection: str = "news_v2",
    source_name: str = "",
    published_at: str = "",
    author: str = "",
    title: str = "",
    content: str | None = None,
    fetch_timeout: float = 12.0,
) -> dict[str, Any]:
    """Fetch + chunk + embed any story URL into the RAG news collection.

    Runs through the existing ingestion pipeline
    (``app.rag.crawler.crawl_and_ingest`` → dedup → ``engine.ingest_document``
    chunk/embed), so the story becomes RAG-knowledgeable with source
    attribution in the point metadata: ``source_url`` / ``source_name`` /
    ``author`` / ``published_at`` / ``fetched_at`` / ``fetch_chain``.

    Args:
        url: The story URL to ingest.
        collection: RAG collection (default ``news_v2`` — the collection the
            bot's RAG lookup and the news router already search).
        source_name: Optional human-readable source (e.g. "CoinDesk").
        published_at: Optional ISO/RSS publish timestamp (used by dedup
            windows); when empty it is taken from the fetched metadata.
        author: Optional byline; when empty it is taken from fetched metadata.
        title: Optional headline; when empty it is taken from fetched metadata.
        content: Pre-extracted body text. When supplied the fetch is skipped
            (useful when the caller already has the article text).
        fetch_timeout: Total budget for the fetch chain when ``content`` is
            not supplied.

    Returns:
        ``{"doc_id", "chunks", "status", "duplicate_level", "group_id"}`` —
        the same shape as ``crawler.crawl_and_ingest``.

    Raises:
        StoryFetchError: When the fetch chain produced no content (and no
            ``content`` was supplied).
    """
    from rugmunchbot._vendor.rag_pkg.crawler import crawl_and_ingest

    if content is None:
        story = await fetch_story(url, timeout=fetch_timeout)
        if not story.ok:
            raise StoryFetchError(story.error or "couldn't fetch")
        content = story.text
        title = title or story.title
        author = author or story.author
        published_at = published_at or story.published_at
        source = story.source
        fetch_chain = story.chain
    else:
        source = "supplied"
        fetch_chain = ["supplied"]

    metadata: dict[str, Any] = {
        "source_url": url,
        "source": source,
        "fetch_chain": fetch_chain,
        "crawler": f"story_ingest:{source}",
        "fetched_at": datetime.now(UTC).isoformat(),
        "title": (title or url)[:500],
    }
    if source_name:
        metadata["source_name"] = source_name
    if author:
        metadata["author"] = author
    if published_at:
        metadata["published_at"] = published_at

    return await crawl_and_ingest(
        url=url,
        collection=collection,
        content=content,
        metadata=metadata,
        published_at=published_at,
    )


__all__ = ["StoryFetch", "StoryFetchError", "fetch_story", "ingest_url"]
