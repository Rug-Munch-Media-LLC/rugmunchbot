# VENDORED 2026-08-17 from rmi-backend/app/rag/pryscraper_ingest.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Pryscraper ↔ Crawl4AI bridge for RAG ingestion.

Strategy (per 2026 crawl standards):
  1. Pryscraper first — fast HTML extraction via trafilatura/markdownify.
  2. Crawl4AI fallback — JS-heavy pages, Cloudflare-protected sites, and
     anything Pryscraper cannot render (dynamic content, SPA pages).

Both paths share the same ingestion entrypoint so the RAG pipeline sees
one unified ``{doc_id, chunks, status}`` shape regardless of crawler.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

log = get_logger(__name__)


async def extract_with_fallback(url: str) -> str:
    """Extract markdown from a URL.

    Tries Pryscraper first (fast, static HTML), then falls back to
    Crawl4AI (JS-heavy / Cloudflare-bypass pages).

    Args:
        url: The page to extract.

    Returns:
        The extracted content as markdown text.

    Raises:
        RuntimeError: If both crawlers fail.
    """
    # 1) Pryscraper — fast HTML extraction
    try:
        from rugmunchbot._vendor.pryscraper_client import PryscraperClient

        client = PryscraperClient()
        try:
            result = await client.scrape(url, formats=["markdown"], js_render=False)
        finally:
            await client.aclose()

        if result.success and result.data:
            markdown = result.data.get("markdown")
            if isinstance(markdown, str) and markdown.strip():
                return markdown
            log.info(
                "pryscraper_empty_content",
                url=url,
                data_keys=list(result.data.keys()) if isinstance(result.data, dict) else type(result.data).__name__,
            )
    except Exception as e:
        log.warning("pryscraper_extract_failed", url=url, error=str(e))

    # 2) Crawl4AI — JS-heavy / Cloudflare bypass
    from rugmunchbot._vendor.rag_pkg.crawler import crawl_crypto_page

    try:
        page = await crawl_crypto_page(url)
        content = str(page.get("content", "") or "")
        if content.strip():
            return content
    except Exception as e:
        log.warning("crawl4ai_extract_failed", url=url, error=str(e))

    raise RuntimeError(f"extract_with_fallback: both crawlers failed for {url}")


async def crawl_and_ingest_with_fallback(url: str, collection: str = "news_v2") -> dict[str, Any]:
    """Extract with the fallback chain and ingest into the RAG engine.

    Args:
        url: The page to extract and ingest.
        collection: RAG collection (default ``news_v2``).

    Returns:
        ``{"doc_id", "chunks", "status"}``.
    """
    from app.rag import crawler
    from rugmunchbot._vendor.rag_pkg.engine import ingest_document

    content = await extract_with_fallback(url)
    result = await ingest_document(
        collection=collection,
        doc_id=crawler._doc_id(url),
        content=content,
        metadata={
            "source_url": url,
            "crawler": "pryscraper_crawl4ai_bridge",
            "crawled_at": datetime.now(UTC).isoformat(),
        },
    )
    return {
        "doc_id": result.get("doc_id", crawler._doc_id(url)),
        "chunks": result.get("chunks", 0),
        "status": result.get("status", "unknown"),
    }
