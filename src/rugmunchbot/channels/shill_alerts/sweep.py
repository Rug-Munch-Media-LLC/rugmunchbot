"""Bounded read of the monitored channels (registry-driven, fail-open).

Reuses the news_ingest headless client and normalizer — the same read path
the existing sweeps use — so this lane adds no second Telegram scraper.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.shill_alerts.registry import load_registry
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = get_logger("rmi_bot.channels.shill_alerts.sweep")


def _msg_id(url: str) -> str:
    tail = str(url or "").rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else ""


async def sweep_channels(
    *,
    handles: Iterable[str] | None = None,
    lookback_hours: int = 6,
    per_channel: int = 30,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Read recent messages from the monitored channels; ``[]`` on any failure.

    Channel list comes from the registry adapter when ``handles`` is omitted.
    A dead channel, a missing client or a malformed row never raises.
    """
    from rugmunchbot._vendor.news_ingest.ingest import read_channel_messages
    from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article
    from rugmunchbot.channels.telegram_news._sources._fetch import _inside_window

    targets = [
        h for h in (handles if handles is not None else (c.handle for c in load_registry())) if h
    ]
    if not targets:
        logger.debug("shill_alerts:no_registry_channels")
        return []

    if client is None:
        try:
            from rugmunchbot.channels.telegram_news._sources._fetch import _build_headless_client

            client = await _build_headless_client()
        except Exception as exc:  # fail-open by contract
            logger.warning("shill_alerts:no_client err=%s", str(exc)[:120])
            return []
    if client is None:
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))
    out: list[dict[str, Any]] = []
    for username in targets[:60]:
        try:
            messages = await read_channel_messages(client, username, per_channel)
        except Exception as exc:  # one dead channel never blocks the sweep
            logger.debug("shill_alerts:channel_failed channel=%s err=%s", username, str(exc)[:80])
            continue
        for message in messages:
            try:
                if not _inside_window(message, cutoff):
                    continue
                article = channel_message_to_article(message, "alpha")
            except Exception as exc:
                logger.debug("shill_alerts:bad_row channel=%s err=%s", username, str(exc)[:80])
                continue
            if article is None:
                continue
            url = str(getattr(article, "source_url", "") or "")
            out.append(
                {
                    "channel": str(getattr(article, "source_channel", "") or username).lstrip("@"),
                    "message_id": _msg_id(url) or str(getattr(message, "id", "") or ""),
                    "text": f"{getattr(article, 'title', '') or ''} "
                    f"{getattr(article, 'body', '') or ''}".strip(),
                    "link": url,
                    "posted_at": str(getattr(article, "posted_at", "") or ""),
                }
            )
    logger.info("shill_alerts:sweep channels=%s messages=%s", len(targets), len(out))
    return out
