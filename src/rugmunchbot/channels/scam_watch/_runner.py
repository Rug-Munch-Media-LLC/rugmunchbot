from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.scam_watch._classify import extract_token_addresses  # noqa: F401
from rugmunchbot.channels.scam_watch._config import (
    logger,
)
from rugmunchbot.channels.scam_watch._discover import sweep_scam_channels
from rugmunchbot.channels.scam_watch._pipeline import (
    _collect_submissions,
    _feed_rag,
    _feed_scanner,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle  # noqa: F401


async def run_scam_watch(ctx: Any = None) -> int:
    """Sweep scam/shill channels and feed extracted token addresses to the scanner.

    Returns the number of unique addresses processed (submitted + enqueued).
    Fails open at every step: no client, no sources, empty sweep, Redis or
    scanner outage all log and return without raising. ``ctx`` is accepted
    for JobQueue compatibility but unused (no posting).
    """
    try:
        articles = await sweep_scam_channels()
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:sweep_failed err=%s", str(exc)[:200])
        return 0
    if not articles:
        logger.debug("scam_watch:no_fresh_items")
        return 0
    submissions = _collect_submissions(articles)
    if not submissions:
        logger.debug("scam_watch:no_token_addresses")
        # Still RAG the articles even without token addresses
        await _feed_rag(articles)
        return 0
    submitted, enqueued = await _feed_scanner(submissions)
    # RAG all scam content so it's searchable by the bot
    rag_count = await _feed_rag(articles)
    logger.info(
        "scam_watch:run_done articles=%s unique_addresses=%s submitted=%s enqueued=%s rag_ingested=%s",
        len(articles),
        len(submissions),
        submitted,
        enqueued,
        rag_count,
    )
    return submitted + enqueued
