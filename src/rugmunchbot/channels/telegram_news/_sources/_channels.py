"""Followed-channel categorization + registry resolution."""

from __future__ import annotations

import os  # noqa: F401
from typing import (  # noqa: F401
    TYPE_CHECKING,
    Any,
    Final,
)

from rugmunchbot._vendor.news_ingest.discovery import discover_followed_channels
from rugmunchbot._vendor.news_ingest.normalize import is_telegram_link  # noqa: F401
from rugmunchbot.channels.telegram_news._sources._config import logger
from rugmunchbot.channels.telegram_news._sources._fetch import (  # noqa: F401
    _build_headless_client,
    _fetch_page,
    _normalize_image_url,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle  # noqa: F401

import sys as _sys

from rugmunchbot.channels.telegram_news._sources._config import (
    _CHANNEL_ALPHA_KEYWORDS,
    _CHANNEL_NEWS_KEYWORDS,
    _CHANNEL_OWN_KEYWORDS,
)

_tg = _sys.modules[__package__.rsplit(".", 1)[0]]


def classify_channel_category(title: str, username: str) -> str:
    """Classify a followed channel by title/username heuristics.

    Returns one of ``news`` | ``alpha`` | ``scam`` | ``other``:

    * ``scam``  — reuses the existing scam/shill classification in
      ``channels/scam_watch.py`` (``_classify_dialog``) for **unambiguous**
      scam signals (pump/shill/airdrop/seed-phrase/rug/honeypot). Channels
      the scam sweep flags only as ``ALPHA_RUG_SHILL`` (alpha/calls/signals/
      gem/100x titles) are *not* excluded here — their call content routes to
      the alpha channel while scam_watch still scans their tokens
      independently. No keyword duplication.
    * ``alpha`` — whale/alpha/signal/calls-style titles → routed to the alpha
      channel.
    * ``news``  — news/media/market-style titles → routed to the news channel.
    * ``other`` — everything else (personal chats, groups, non-crypto) →
      excluded from the sweep entirely.

    Pure + fail-open (a broken scam_watch import never breaks categorization).
    """
    haystack = f"{title or ''} {username or ''}".lower()
    if any(word in haystack for word in _CHANNEL_OWN_KEYWORDS):
        return "other"
    try:
        from rugmunchbot.channels.scam_watch import (
            ScamSourceType,
            _classify_dialog,
        )

        subtype = _classify_dialog(str(title or ""), str(username or ""))
        if subtype is not None and subtype != ScamSourceType.ALPHA_RUG_SHILL:
            return "scam"
    except Exception:  # fail-open by contract
        logger.debug("tg_news:scam_classify_unavailable", exc_info=True)
    if any(kw in haystack for kw in _CHANNEL_ALPHA_KEYWORDS):
        return "alpha"
    if any(kw in haystack for kw in _CHANNEL_NEWS_KEYWORDS):
        return "news"
    return "other"


def channel_sources() -> list[dict[str, Any]]:
    """Source-config dicts (``username`` + ``category``) for the sweep.

    Returns the explicit ``RMI_TG_NEWS_CHANNELS`` list (falling back to
    ``_DEFAULT_CHANNELS``). This is the *fallback* list — when auto-discovery
    is enabled and the headless client is available,
    :func:`resolve_channel_sources` prefers the account's own channels.
    """
    return [{"username": u, "category": "crypto_news", "name": u} for u in _tg.TG_NEWS_CHANNELS]


async def resolve_channel_sources(client: Any | None = None) -> list[dict[str, Any]]:
    """Resolve the sweep's channel list, preferring the account's own channels.

    Priority (highest first):
      1. **Discovered** — the logged-in account's followed/member public
         channels, enumerated via Telethon ``get_dialogs()``
         (:func:`app.domains.news_ingest.discover_followed_channels`), gated
         by ``RMI_TG_NEWS_AUTO_DISCOVER`` (default on). Every discovered
         channel is categorized by :func:`classify_channel_category`:
         **news** → ``crypto_news`` and **alpha** → ``alpha`` are swept;
         **scam** channels are left to the scam_watch job (no duplication)
         and **other** channels (personal chats, non-crypto) are skipped.
         The result is bounded by ``RMI_TG_NEWS_MAX_CHANNELS``.
      2. **Explicit** — ``RMI_TG_NEWS_CHANNELS`` env list.
      3. **Default** — ``_DEFAULT_CHANNELS``.

    Fail-open: when auto-discovery is disabled, the client is missing, or
    discovery returns nothing, the explicit/default list is used.
    """
    if _tg.TG_NEWS_AUTO_DISCOVER and client is not None:
        try:
            discovered = await discover_followed_channels(client)
        except Exception as exc:  # discovery must never break the sweep
            logger.warning("tg_news:auto_discover_failed err=%s", str(exc)[:200])
            discovered = []
        if discovered:
            sources: list[dict[str, Any]] = []
            seen: set[str] = set()
            counts = {"news": 0, "alpha": 0, "scam": 0, "other": 0}
            for username, title in discovered:
                handle = str(username or "").strip().lstrip("@")
                if not handle or handle in seen:
                    continue
                seen.add(handle)
                category = classify_channel_category(str(title or ""), handle)
                counts[category] += 1
                if category in ("scam", "other"):
                    continue  # scam → scam_watch; other → not news-relevant
                category_tag = "crypto_news" if category == "news" else "alpha"
                sources.append(
                    {
                        "username": handle,
                        "category": category_tag,
                        "name": str(title or handle),
                        "channel_category": category,
                    }
                )
                if len(sources) >= _tg.TG_NEWS_MAX_CHANNELS:
                    break
            if sources:
                logger.info(
                    "tg_news:using_discovered_channels count=%s by_category=%s",
                    len(sources),
                    counts,
                )
                return sources
            logger.debug(
                "tg_news:auto_discover_no_news_channels fallback_to_explicit counts=%s", counts
            )
    return channel_sources()
