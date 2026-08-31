"""RAG ingestion + threat-intel persistence for scraped Telegram messages.

Two sinks, both fail-open:
  * ``telegram_social`` Qdrant collection — EVERY message (not just scams)
    for total social awareness / RAG. Dedup is structural: the stable
    ``doc_id`` (``tg:{channel}:{message_id}``) plus the ingest engine's
    chunk-hash dedup means re-running a sweep never double-embeds.
  * Scam detections → ``threat_intel_v2`` (same collection the X-shill lane
    uses) + the backend ``shill_evidence`` store (source='telegram'), with
    the shiller's handle, the scam token, and evidence (screenshot path when
    media was downloaded).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rugmunchbot.channels.tg_scraper._config import (
    TG_SCRAPER_RAG,
    TG_SCRAPER_RAG_COLLECTION,
    TG_SCRAPER_THREAT,
    logger,
)

#: Redis SET for cross-run message dedup (channel:msg_id keys).
_SEEN_KEY = "bot:tg_scraper:seen"
_SEEN_CACHE: set[str] = set()
_MAX_SEEN_CACHE = 5000


def message_key(message: Any) -> str:
    """Stable dedup key for a scraped message. Never raises."""
    try:
        channel = (message.channel_username or message.channel or "unknown").lstrip("@")
        return f"{channel}:{message.message_id}"
    except Exception:
        return ""


async def _mark_seen(key: str) -> bool:
    """Return True when *key* is new (and record it). Best-effort, never raises.

    Mirrors the scam_watch seen-cache: bounded in-memory set + Redis SET for
    cross-process/cross-run dedup. Redis down → in-memory still applies.
    """
    if not key:
        return False
    if key in _SEEN_CACHE:
        return False
    _SEEN_CACHE.add(key)
    if len(_SEEN_CACHE) > _MAX_SEEN_CACHE:
        _SEEN_CACHE.clear()
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is not None:
            added = await r.sadd(_SEEN_KEY, key)
            if not added:
                return False
    except Exception as exc:  # fail-open: in-memory dedupe still applies
        logger.debug("tg_scraper:seen_failed err=%s", str(exc)[:120])
    return True


def _message_doc(message: Any, detection: Any) -> Any:
    """Build one RAG Document for a scraped message (stable doc_id)."""
    from rugmunchbot._vendor.rag_pkg.ingestion import Document

    channel = (message.channel_username or message.channel or "unknown").lstrip("@")
    text = (message.text or "").strip()
    sender = message.sender_handle or message.sender_name or "unknown"
    header = f"Source: telegram/{channel}\nSender: {sender}\n"
    if message.forwarded_from:
        header += f"Forwarded from: {message.forwarded_from}\n"
    doc_id = f"tg:{channel}:{message.message_id}"
    return Document(
        content=f"{header}{text}"[:8000],
        doc_id=doc_id,
        collection=TG_SCRAPER_RAG_COLLECTION,
        metadata={
            "source": "telegram",
            "channel": channel,
            "message_id": message.message_id,
            "sender": sender,
            "sender_id": message.sender_id,
            "posted_at": message.posted_at.isoformat() if message.posted_at else None,
            "media_type": message.media_type,
            "media_path": message.media_path,
            "forwarded_from": message.forwarded_from,
            "reply_to": message.reply_to,
            "suspicious": bool(detection and detection.is_suspicious),
            "ingested_at": datetime.now(UTC).isoformat(),
        },
        source_type="telegram_social",
    )


async def ingest_to_rag(messages: list[Any], detections: list[Any] | None = None) -> int:
    """Embed scraped messages into the ``telegram_social`` RAG collection.

    Dedup on (channel, message_id) via the seen-set; the stable doc_id plus
    the engine's chunk-hash dedup makes re-runs idempotent. Fail-open: any
    RAG outage logs and returns the partial count.
    """
    if not TG_SCRAPER_RAG or not messages:
        return 0
    try:
        from rugmunchbot._vendor.rag_pkg.ingestion import BatchIngestor

        ingestor = BatchIngestor()
    except Exception as exc:
        logger.warning("tg_scraper:rag_unavailable err=%s", str(exc)[:200])
        return 0
    ingested = 0
    for index, message in enumerate(messages):
        key = message_key(message)
        if not await _mark_seen(key):
            continue
        detection = detections[index] if detections and index < len(detections) else None
        try:
            doc = _message_doc(message, detection)
            report = await ingestor.ingest_batch([doc])
            if report.accepted:
                ingested += report.accepted
        except Exception as exc:
            logger.debug("tg_scraper:rag_doc_failed key=%s err=%s", key, str(exc)[:120])
    if ingested:
        logger.info("tg_scraper:rag_ingested count=%s", ingested)
    return ingested


def _evidence_record(message: Any, detection: Any) -> dict[str, Any]:
    """Build the backend shill-evidence record for one scam detection."""
    channel = (message.channel_username or message.channel or "unknown").lstrip("@")
    token = (
        ",".join((detection.tickers or [])[:3])
        or ((detection.evm_addresses + detection.solana_addresses) or [""])[0]
    )
    chain = "solana" if detection.solana_addresses and not detection.evm_addresses else "ethereum"
    patterns = ",".join(h.pattern_id for h in detection.hits)
    return {
        "token": str(token)[:80],
        "chain": chain,
        "account": message.sender_handle or message.sender_name or f"tg:{channel}",
        "x_url": f"https://t.me/{channel}/{message.message_id}",
        "url": f"https://t.me/{channel}/{message.message_id}",
        "image_path": message.media_path or "",
        "text": (message.text or "")[:2000],
        "ocr_text": (message.text or "")[:2000],
        "source": "telegram",
        "channel": channel,
        "patterns": patterns,
        "known_scammer": detection.known_scammer,
    }


async def persist_scam_detections(messages: list[Any], detections: list[Any]) -> int:
    """Persist suspicious messages to threat_intel_v2 + shill evidence.

    Backend-first (``shill_api.add_evidence`` — the same store the X-shill
    lane writes), then a threat_intel_v2 RAG doc per detection. Fail-open:
    every hop logs and continues; returns the number of detections persisted
    to at least one sink.
    """
    if not TG_SCRAPER_THREAT:
        return 0
    persisted = 0
    for message, detection in zip(messages, detections, strict=False):
        if not detection.is_suspicious:
            continue
        record = _evidence_record(message, detection)
        landed = False
        # Backend evidence store (thin consumer, mirrors shill_watch).
        try:
            from rugmunchbot.services import shill_api

            result = await shill_api.add_evidence(record)
            if result and result.get("saved"):
                landed = True
        except Exception as exc:
            logger.debug("tg_scraper:evidence_backend_failed err=%s", str(exc)[:120])
        # threat_intel_v2 RAG doc (same collection as the scam_watch lane).
        try:
            from rugmunchbot._vendor.rag_pkg.ingestion import BatchIngestor, Document

            channel = record["channel"]
            content = (
                f"Source: telegram/{channel}\n"
                f"Sender: {record['account']}\n"
                f"Patterns: {record['patterns']}\n"
                f"Token: {record['token']}\n"
                f"URL: {record['url']}\n"
                f"{record['text']}"
            )[:8000]
            doc = Document(
                content=content,
                doc_id=f"tgscam:{channel}:{message.message_id}",
                collection="threat_intel_v2",
                metadata={
                    "source": "telegram",
                    "kind": "shill_evidence",
                    "channel": channel,
                    "account": record["account"],
                    "token": record["token"],
                    "patterns": record["patterns"],
                    "url": record["url"],
                    "image_path": record["image_path"],
                    "ingested_at": datetime.now(UTC).isoformat(),
                },
                source_type="telegram_scam",
            )
            ingestor = BatchIngestor()
            report = await ingestor.ingest_batch([doc])
            if report.accepted:
                landed = True
        except Exception as exc:
            logger.debug("tg_scraper:threat_rag_failed err=%s", str(exc)[:160])
        if landed:
            persisted += 1
            logger.info(
                "tg_scraper:scam_persisted channel=%s sender=%s patterns=%s",
                record["channel"],
                record["account"],
                record["patterns"],
            )
    return persisted
