"""t.me link resolution — instant-view / OG canonical target extraction."""

from __future__ import annotations

import os  # noqa: F401
from typing import (  # noqa: F401
    TYPE_CHECKING,
    Any,
    Final,
)

from rugmunchbot._vendor.news_ingest.discovery import discover_followed_channels  # noqa: F401
from rugmunchbot._vendor.news_ingest.normalize import is_telegram_link
from rugmunchbot.channels.telegram_news._sources._config import logger  # noqa: F401
from rugmunchbot.channels.telegram_news._sources._fetch import (  # noqa: F401
    _build_headless_client,
    _fetch_page,
    _normalize_image_url,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle  # noqa: F401

import re

_TME_RESOLVE_TIMEOUT: float = 5.0


_TME_IN_TEXT_RE: Final = re.compile(r"https?://(?:t\.me|telegram\.me)/", re.IGNORECASE)


_TME_OG_URL_RES: Final = (
    re.compile(
        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']', re.IGNORECASE
    ),
)


_TME_CANONICAL_RES: Final = (
    re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']', re.IGNORECASE),
)


_TME_HREF_RES: Final = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)


def _message_has_tme_link(article: NewsArticle) -> bool:
    """True when the message TEXT carries a t.me / telegram.me link.

    Only messages whose own text links to a Telegram page are candidates for
    dereferencing — a synthesized ``source_url`` fallback (created by
    normalize when the message had no link at all) is never dereferenced.
    """
    return bool(_TME_IN_TEXT_RE.search(f"{article.title or ''} {article.body or ''}"))


def _extract_tme_target(html: str, base_url: str) -> str | None:
    """First usable real-article URL inside a t.me page (pure, fail-open).

    Priority: og:url → rel=canonical → first external http(s) href. A
    candidate that is still a Telegram link (the page's own share URL) is
    skipped so the real article link wins.
    """
    for pattern in _TME_OG_URL_RES:
        match = pattern.search(html)
        if match:
            candidate = _normalize_image_url(match.group(1), base_url)
            if candidate and not is_telegram_link(candidate):
                return candidate
    for pattern in _TME_CANONICAL_RES:
        match = pattern.search(html)
        if match:
            candidate = _normalize_image_url(match.group(1), base_url)
            if candidate and not is_telegram_link(candidate):
                return candidate
    for match in _TME_HREF_RES.finditer(html):
        candidate = _normalize_image_url(match.group(1), base_url)
        if candidate and not is_telegram_link(candidate):
            return candidate
    return None


async def resolve_tme_link(url: str) -> str:
    """Dereference a t.me link to the real article URL (fail-open).

    Fetches the t.me page (direct, 5s timeout; WAF-blocked pages escalate to
    the proxy ladder — FlareSolverr / rotating pool when configured) and
    extracts the real article URL from ``og:url`` / ``rel=canonical`` / the
    first external http(s) link. Returns the resolved URL, or the original
    *url* unchanged on any failure (network error, timeout, non-200, page
    without a real link). Never raises — a slow publisher never blocks the
    sweep.
    """
    url = (url or "").strip()
    if not is_telegram_link(url):
        return url
    html = await _fetch_page(url, _TME_RESOLVE_TIMEOUT)
    if not html:
        return url
    return _extract_tme_target(html, url) or url
