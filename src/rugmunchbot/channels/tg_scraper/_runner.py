"""Periodic all-dialogs Telegram scraper — entry point.

Sweeps EVERY dialog the headless account is a member of (default 24h
lookback), runs scam/shill detection over each message, ingests everything
into the ``telegram_social`` RAG collection, and persists scam detections to
threat_intel_v2 + shill evidence. Registered on the PTB JobQueue every
``RMI_TG_SCRAPER_INTERVAL_MINUTES`` (default 30).

Fail-open at every step: no client, no dialogs, RAG/Redis/backend outages
all log and return without raising.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.tg_scraper._config import (
    TG_SCRAPER_LOOKBACK_HOURS,
    TG_SCRAPER_MEDIA_BUDGET,
    TG_SCRAPER_PER_CHANNEL_LIMIT,
    logger,
)


async def run_tg_scraper(ctx: Any = None) -> dict[str, int]:
    """Sweep all dialogs → detect → ingest → persist. Never raises.

    ``ctx`` is accepted for JobQueue compatibility but unused (no posting).
    Returns run counters for tests/observability.
    """
    from rugmunchbot.channels.tg_scraper._detect import detect_message
    from rugmunchbot.channels.tg_scraper._ingest import (
        ingest_to_rag,
        persist_scam_detections,
    )
    from rugmunchbot.channels.tg_scraper._scrape import (
        _build_headless_client,
        scrape_all_dialogs,
    )

    counts = {"messages": 0, "suspicious": 0, "rag": 0, "threat": 0}
    try:
        client = await _build_headless_client()
    except Exception as exc:
        logger.warning("tg_scraper:client_failed err=%s", str(exc)[:200])
        return counts
    if client is None:
        logger.debug("tg_scraper:no_client")
        return counts
    try:
        messages = await scrape_all_dialogs(
            client,
            lookback_hours=TG_SCRAPER_LOOKBACK_HOURS,
            per_channel_limit=TG_SCRAPER_PER_CHANNEL_LIMIT,
            media_budget=TG_SCRAPER_MEDIA_BUDGET,
        )
    except Exception as exc:
        logger.warning("tg_scraper:sweep_failed err=%s", str(exc)[:200])
        return counts
    counts["messages"] = len(messages)
    if not messages:
        logger.debug("tg_scraper:no_fresh_messages")
        return counts

    detections = []
    for message in messages:
        try:
            detections.append(await detect_message(message))
        except Exception as exc:
            logger.debug(
                "tg_scraper:detect_failed msg=%s err=%s", message.message_id, str(exc)[:120]
            )
            from rugmunchbot.channels.tg_scraper._detect import Detection

            detections.append(Detection())
    counts["suspicious"] = sum(1 for d in detections if d.is_suspicious)

    counts["rag"] = await ingest_to_rag(messages, detections)
    counts["threat"] = await persist_scam_detections(messages, detections)
    logger.info(
        "tg_scraper:run_done messages=%d suspicious=%d rag=%d threat=%d",
        counts["messages"],
        counts["suspicious"],
        counts["rag"],
        counts["threat"],
    )
    return counts
