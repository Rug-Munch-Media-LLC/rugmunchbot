from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot.channels.scam_watch._classify import extract_token_addresses
from rugmunchbot.channels.scam_watch._config import (  # noqa: F401
    _DIALOG_HEURISTICS,
    _OWN_EXCLUDE_KEYWORDS,
    SCAM_CHANNELS,
    SCAM_WATCH_ENABLED,
    SCAM_WATCH_ENQUEUE,
    SCAM_WATCH_INCLUDE_WATCHERS,
    SCAM_WATCH_PER_CHANNEL_LIMIT,
    SCAM_WATCH_QUEUE_LIMIT,
    SCAM_WATCH_SCAN_LIMIT,
    SCAM_WATCH_SWEEP_HOURS,
    ScamSourceType,
    logger,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle  # noqa: F401


def _collect_submissions(articles: list[NewsArticle]) -> list[dict[str, Any]]:
    """Flatten articles → unique (address, chain, attribution) records."""
    submissions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for article in articles or []:
        addresses, tickers = extract_token_addresses(article)
        for addr in addresses:
            key = f"{addr['chain']}:{addr['address']}"
            if key in seen:
                continue
            seen.add(key)
            submissions.append(
                {
                    "address": addr["address"],
                    "chain": addr["chain"],
                    "ticker": ",".join(tickers[:5]),
                    "source_channel": article.source_channel,
                    "source_url": article.source_url,
                    "category": article.category or "scam_shill",
                }
            )
    return submissions


_SEEN_KEY = "bot:scam_watch:seen"


_SCAN_QUEUE_KEY = "bot:scam_watch:scan_queue"


_SEEN_CACHE: set[str] = set()


_MAX_SEEN_CACHE = 2000


async def _mark_seen(address: str) -> bool:
    """Return True when *address* is new (and record it). Best-effort, never raises.

    Backed by the Redis SET ``bot:scam_watch:seen`` plus a bounded in-memory
    cache (so cross-process dedup survives even when Redis is down).
    """
    key = address.strip().lower()
    if not key:
        return False
    if key in _SEEN_CACHE:
        return False
    _SEEN_CACHE.add(key)
    if len(_SEEN_CACHE) > _MAX_SEEN_CACHE:
        _SEEN_CACHE.clear()
    try:
        r = get_redis_async()
        if r is not None:
            added = await r.sadd(_SEEN_KEY, key)
            if not added:
                return False
    except Exception as exc:  # fail-open: in-memory dedupe still applies
        logger.debug("scam_watch:seen_failed err=%s", str(exc)[:120])
    return True


async def submit_for_scan(address: str, chain: str = "ethereum") -> bool:
    """Best-effort in-process scanner submission (free tier). Never raises.

    Calls ``app.domains.scanner.service.scan_token`` — the same service the
    ``POST /api/v1/scanner/scan`` endpoint triggers. Fail-open: any import /
    runtime error logs and returns ``False``.
    """
    try:
        # Phase 2d degod: HTTP scanner tool (same backend service, called
        # over the API like every interactive scan) instead of the in-process
        # app.domains.scanner.service import.
        from rugmunchbot.tools.scanner._scans import scan_token as _http_scan

        result = await _http_scan(address, chain=chain, security_only=True)
        if not result:
            return False
        flags = list(result.get("risk_flags") or [])
        safety = result.get("safety_score")
        logger.info(
            "scam_watch:scan_done address=%s chain=%s safety=%s flags=%s",
            address[:12],
            chain,
            safety,
            flags[:5],
        )
        return True
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:scan_failed address=%s err=%s", address[:12], str(exc)[:120])
        return False


async def _enqueue_for_scanner(entry: dict[str, Any]) -> bool:
    """Push one discovered address onto the scanner queue (JSON, best-effort)."""
    try:
        r = get_redis_async()
        if r is None:
            return False
        await r.lpush(_SCAN_QUEUE_KEY, json.dumps(entry, default=str))
        return True
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:enqueue_failed err=%s", str(exc)[:120])
        return False


async def _feed_scanner(submissions: list[dict[str, Any]]) -> tuple[int, int]:
    """Submit new addresses to the scanner + enqueue durable records.

    Returns ``(submitted, enqueued)``. Both paths are best-effort and
    independently bounded (``RMI_SCAM_WATCH_SCAN_LIMIT`` /
    ``RMI_SCAM_WATCH_QUEUE_LIMIT``; ``RMI_SCAM_WATCH_ENQUEUE`` disables the
    queue). Only addresses not already in the seen-set are processed.
    """
    scan_budget = max(0, SCAM_WATCH_SCAN_LIMIT)
    queue_budget = max(0, SCAM_WATCH_QUEUE_LIMIT) if SCAM_WATCH_ENQUEUE else 0
    submitted = 0
    enqueued = 0
    for entry in submissions or []:
        if scan_budget <= 0 and queue_budget <= 0:
            break
        address = str(entry.get("address") or "").strip()
        if not address:
            continue
        if not await _mark_seen(address):
            continue  # already known from a previous run
        if scan_budget > 0:
            await submit_for_scan(address, str(entry.get("chain") or "ethereum"))
            submitted += 1
            scan_budget -= 1
        if queue_budget > 0:
            if await _enqueue_for_scanner(entry):
                enqueued += 1
            queue_budget -= 1
    return submitted, enqueued


async def _feed_rag(articles: list[NewsArticle]) -> int:
    """Ingest scam channel articles into the RAG threat_intel_v2 collection.

    Every swept scam/shill article is embedded so the bot can answer
    questions about known scam callers, shill channels, and rug operations.
    Fail-open: any RAG outage logs and returns 0.
    """
    if not articles:
        return 0
    try:
        from rugmunchbot._vendor.rag_pkg.ingestion import BatchIngestor, Document

        ingestor = BatchIngestor()
        ingested = 0
        for art in articles:
            title = (art.title or "Scam Alert").strip()
            body = (art.body or art.text or "").strip()
            if not body and not title:
                continue
            source = art.source_channel or "telegram_scam"
            url = art.source_url or ""
            content = f"Source: {source}\nTitle: {title}\n{body}"
            if url:
                content = f"URL: {url}\n{content}"
            doc = Document(
                content=content[:8000],
                doc_id=f"scam:{source}:{hash(content[:500])}",
                collection="threat_intel_v2",
                metadata={
                    "source": source,
                    "category": "scam_shill",
                    "channel": source,
                    "url": url,
                    "ingested_at": datetime.now(UTC).isoformat(),
                },
                source_type="telegram_scam",
            )
            report = await ingestor.ingest_batch([doc])
            if report.accepted:
                ingested += report.accepted
        if ingested:
            logger.info("scam_watch:rag_ingested count=%s", ingested)
        return ingested
    except Exception as exc:
        logger.warning("scam_watch:rag_ingest_failed err=%s", str(exc)[:200])
        return 0
