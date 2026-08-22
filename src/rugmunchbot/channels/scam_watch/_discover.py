from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.news_ingest.ingest import dedupe_articles, read_channel_messages
from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article
from rugmunchbot.channels.scam_watch._config import (  # noqa: F401
    _DIALOG_HEURISTICS,
    _OWN_EXCLUDE_KEYWORDS,
    SCAM_CHANNELS,
    SCAM_WATCH_ENABLED,
    SCAM_WATCH_EXCLUDE,
    SCAM_WATCH_INCLUDE_WATCHERS,
    SCAM_WATCH_MAX_DISCOVERED,
    SCAM_WATCH_PER_CHANNEL_LIMIT,
    SCAM_WATCH_SWEEP_HOURS,
    ScamSourceType,
    logger,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle


async def _build_headless_client() -> Any:
    """Return the singleton headless Telethon client, or ``None`` (never raises)."""
    try:
        from rugmunchbot._vendor.headless import get_headless_client

        client = await get_headless_client()
        if getattr(client, "is_connected", False):
            return client
        return None
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:headless_client_unavailable err=%s", str(exc)[:200])
        return None


def _as_utc(value: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _inside_window(message: ChannelMessage, cutoff: datetime) -> bool:
    """True when a message falls inside the lookback window.

    Messages without a date are kept (best-effort — cross-run dedup on the
    seen-set still prevents re-ingesting ancient items).
    """
    posted = message.posted_at
    if posted is None:
        return True
    return _as_utc(posted) >= cutoff


def _registry_scam_sources(include_watchers: bool | None = None) -> list[dict[str, Any]]:
    """Scam/shill channels from the followed-channels registry (tg_channels.json).

    Picks up ``kind=scammer_caller`` / ``kind=scammer_group`` entries and
    ``category=shill_promotions`` entries — channels the owner already
    flagged as shill/caller sources. Legit scam-*watcher* categories
    (``scam_alerts`` / ``phishing_alerts``) are excluded unless
    ``include_watchers=True``. Fail-open → ``[]``.
    """
    try:
        from rugmunchbot._vendor.tg_channel_registry import load_tg_channels
    except ImportError:
        return []
    include_watchers = SCAM_WATCH_INCLUDE_WATCHERS if include_watchers is None else include_watchers
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        entries = load_tg_channels()
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:registry_failed err=%s", str(exc)[:200])
        return []
    for entry in entries:
        kind = str(entry.get("kind") or "")
        category = str(entry.get("category") or "")
        subtype: ScamSourceType | None = None
        if kind == "scammer_caller":
            subtype = ScamSourceType.SHILL_CHANNEL
        elif kind == "scammer_group":
            subtype = ScamSourceType.PUMP_GROUP
        elif category == "shill_promotions":
            subtype = ScamSourceType.SHILL_CHANNEL
        elif include_watchers and category in ("scam_alerts", "phishing_alerts"):
            subtype = ScamSourceType.SCAM_TOKEN_CHANNEL
        if subtype is None:
            continue
        key = str(entry.get("username") or "").strip().lstrip("@").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "username": key,
                "name": str(entry.get("name") or key),
                "category": "scam_shill",
                "subtype": subtype,
                "registry": True,
            }
        )
    return sources


def _classify_dialog(title: str, username: str) -> ScamSourceType | None:
    """Classify a followed channel (title/username) via shill heuristics.

    Returns ``None`` for own channels, user-excluded handles, and channels
    that do not match any shill keyword.
    """
    haystack = f"{title or ''} {username or ''}".lower()
    if any(word in haystack for word in _OWN_EXCLUDE_KEYWORDS):
        return None
    if any(word in haystack for word in SCAM_WATCH_EXCLUDE):
        return None
    for subtype, keywords in _DIALOG_HEURISTICS:
        if any(keyword in haystack for keyword in keywords):
            return subtype
    return None


async def discover_scam_channels(
    client: Any, max_discovered: int | None = None
) -> list[dict[str, Any]]:
    """Auto-discover scam/shill channels from the followed dialogs list.

    Reuses ``app.domains.news_ingest.discovery.discover_followed_channels``
    (the canonical followed-channels mechanism — same headless client the
    news sweep reads through) and flags dialogs whose title/username matches
    shill keyword heuristics. Fail-open → ``[]``; bounded by
    ``max_discovered`` (default ``RMI_SCAM_WATCH_MAX_DISCOVERED``).
    """
    cap = max(0, max_discovered if max_discovered is not None else SCAM_WATCH_MAX_DISCOVERED)
    if cap <= 0:
        return []
    try:
        from rugmunchbot._vendor.news_ingest.discovery import discover_followed_channels
    except ImportError:
        return []
    try:
        pairs = await discover_followed_channels(client)
    except Exception as exc:  # fail-open by contract
        logger.warning("scam_watch:discovery_failed err=%s", str(exc)[:200])
        return []
    sources: list[dict[str, Any]] = []
    for username, title in pairs:
        subtype = _classify_dialog(str(title or ""), str(username or ""))
        if subtype is None:
            continue
        handle = str(username or "").strip().lstrip("@").lower()
        if not handle:
            continue
        sources.append(
            {
                "username": handle,
                "name": str(title or handle),
                "category": "scam_shill",
                "subtype": subtype,
                "discovered": True,
            }
        )
        if len(sources) >= cap:
            break
    if sources:
        logger.info("scam_watch:discovered count=%s", len(sources))
    return sources


async def channel_sources(client: Any | None = None) -> list[dict[str, Any]]:
    """Effective source-config dicts (``username`` + ``category`` + ``subtype``).

    Order: explicit ``RMI_SCAM_CHANNELS`` → registry scam/shill entries →
    auto-discovered dialogs. Deduped by normalized @handle; never raises.
    """
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    for username in SCAM_CHANNELS:
        key = str(username).strip().lstrip("@").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "username": key,
                "name": key,
                "category": "scam_shill",
                "subtype": ScamSourceType.SHILL_CHANNEL,
                "explicit": True,
            }
        )

    for entry in _registry_scam_sources():
        key = str(entry.get("username") or "")
        if key in seen:
            continue
        seen.add(key)
        sources.append(entry)

    for entry in await discover_scam_channels(client):
        key = str(entry.get("username") or "")
        if key in seen:
            continue
        seen.add(key)
        sources.append(entry)

    return sources


async def sweep_scam_channels(
    *,
    client: Any | None = None,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
    max_discovered: int | None = None,
) -> list[NewsArticle]:
    """Read recent messages from the scam/shill channel sources.

    Every normalized item is tagged ``category="scam_shill"`` and carries
    the canonical entity list (``$TICKER`` / EVM / Solana — via
    ``extract_entities``). Returns deduped ``NewsArticle`` objects newer
    than ``lookback_hours`` (default ``RMI_SCAM_WATCH_SWEEP_HOURS``).
    Accepts an injected client (used by tests); when omitted the headless
    singleton is built (fail-open → ``[]``).
    """
    if client is None:
        client = await _build_headless_client()
    if client is None:
        logger.warning("scam_watch:sweep_skipped_no_client")
        return []

    sources = await channel_sources(client)
    if not sources:
        logger.info("scam_watch:no_sources_configured")
        return []

    lookback = max(1, lookback_hours or SCAM_WATCH_SWEEP_HOURS)
    per_channel = max(5, per_channel_limit or SCAM_WATCH_PER_CHANNEL_LIMIT)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)

    articles: list[NewsArticle] = []
    for source in sources:
        username = str(source.get("username") or "").strip().lstrip("@")
        if not username:
            continue
        messages = await read_channel_messages(client, username, per_channel)
        for message in messages:
            if not _inside_window(message, cutoff):
                continue
            article = channel_message_to_article(message, "scam_shill")
            if article is not None:
                articles.append(article)
    return dedupe_articles(articles)
