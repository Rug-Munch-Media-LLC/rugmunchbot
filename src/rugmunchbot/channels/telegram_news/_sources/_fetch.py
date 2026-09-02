"""Fail-open fetch layer — headless client, WAF escalation, OG images."""

from __future__ import annotations

import os
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
)

from rugmunchbot._vendor.news_ingest.discovery import discover_followed_channels  # noqa: F401
from rugmunchbot._vendor.news_ingest.normalize import is_telegram_link
from rugmunchbot.channels.telegram_news._sources._config import logger
from rugmunchbot.support.image_policy import is_valid_image

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle

import re
from datetime import UTC, datetime


async def _build_headless_client() -> Any:
    """Return the singleton headless Telethon client, or ``None`` (never raises).

    When telethon is missing or the gopass API credentials are unpopulated the
    existing client raises; we log the exact setup needed and degrade.
    """
    try:
        from rugmunchbot._vendor.headless import get_headless_client

        client = await get_headless_client()
        if getattr(client, "is_connected", False):
            return client
        return None
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_news:headless_client_unavailable err=%s", str(exc)[:200])
        return None


def _as_utc(value: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _inside_window(message: ChannelMessage, cutoff: datetime) -> bool:
    """True when a message falls inside the lookback window.

    Messages without a date are kept (best-effort — cross-run dedup on the
    stable ``news_id`` still prevents re-ingesting ancient items).
    """
    posted = message.posted_at
    if posted is None:
        return True
    return _as_utc(posted) >= cutoff


_OG_IMAGE_TIMEOUT: float = 3.0


_OG_HTML_MAX_CHARS: int = 512_000


_OG_IMAGE_RES: Final = (
    re.compile(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.IGNORECASE
    ),
    re.compile(
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.IGNORECASE
    ),
)


FLARESOLVERR_URL: Final[str] = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8192/v1").rstrip("/")


_FLARE_TIMEOUT: float = 15.0


_WAF_MARKERS: Final[tuple[str, ...]] = (
    "cf-chl-",
    "challenge-platform",
    "just a moment",
    "attention required",
    "enable javascript and cookies",
    "verify you are human",
    "checking your browser",
)


_ESCALATE_STATUS: Final[frozenset[int]] = frozenset({403, 429, 401, 503})


def _is_waf_block(status: int | None, body: str) -> bool:
    """True when the response looks like a WAF/challenge block (not real HTML)."""
    if status in _ESCALATE_STATUS:
        return True
    if status != 200:
        return False
    if len(body) > 50_000:
        return False  # a real page, not a challenge stub
    lowered = body.lower()
    return any(marker in lowered for marker in _WAF_MARKERS)


async def _fetch_via_flaresolverr(url: str) -> str | None:
    """Best-effort FlareSolverr fetch (fail-open → ``None``).

    POSTs the standard ``{"cmd": "request.get", ...}`` payload to
    ``FLARESOLVERR_URL`` and returns the solved HTML body capped at
    ``_OG_HTML_MAX_CHARS``. Never raises.
    """
    try:
        import httpx

        payload = {"cmd": "request.get", "url": url, "maxTimeout": int(_FLARE_TIMEOUT * 1000)}
        async with httpx.AsyncClient(
            timeout=_FLARE_TIMEOUT, follow_redirects=False, headers={"User-Agent": "rmi-news/1.0"}
        ) as client:
            response = await client.post(FLARESOLVERR_URL, json=payload)
            if not response.is_success:
                return None
            data = response.json()
        solution = data.get("solution") or {}
        body = solution.get("response")
        if isinstance(body, str) and body.strip():
            return body[:_OG_HTML_MAX_CHARS]
        return None
    except Exception:  # fail-open by contract
        logger.debug("tg_news:flare_fetch_failed url=%s", url[:120], exc_info=True)
        return None


async def _fetch_page(url: str, timeout: float) -> str | None:
    """Fetch *url*'s HTML, escalating to the best available egress.

    Ladder (fail-open, never raises):
      1. **Direct** httpx GET with a browser-ish UA, bounded by *timeout*.
      2. **Rotating residential pool** (``app.databus.proxy_rotator``) when
         ``WEBSHARE_PROXY_LIST`` is configured (gopass ``webshare_proxy_list``
         → env at deploy; absent → tier disabled → direct, fail-open). Retries
         once on the next proxy after a failure, then falls through.
      3. **FlareSolverr** at ``FLARESOLVERR_URL`` for WAF-blocked fetches —
         the live fleet instance on ``127.0.0.1:8192``.

    Returns the HTML body (capped at ``_OG_HTML_MAX_CHARS``) or ``None``.
    Used by both og:image extraction and t.me dereference so a
    Cloudflare-blocked publisher page still resolves without stalling the
    sweep.
    """
    body: str | None = None
    status: int | None = None
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": "rmi-news/1.0"}
        ) as client:
            response = await client.get(url)
            status = int(response.status_code)
            if status == 200:
                body = response.text[:_OG_HTML_MAX_CHARS]
    except Exception:
        logger.debug("tg_news:direct_fetch_failed url=%s", url[:120], exc_info=True)

    if body is not None and not _is_waf_block(status, body):
        return body

    # 2) Rotating residential pool — only active when the deployment sets
    # WEBSHARE_PROXY_LIST (gopass webshare_proxy_list → env at deploy; absent
    # → rotator disabled → skip, direct egress preserved). Cascade per
    # app.databus.proxy_rotator: proxy → next proxy (once) → fall through to
    # FlareSolverr. Credentials are never logged.
    try:
        from rugmunchbot._vendor.proxy_rotator import get_proxy_rotator

        rotator = get_proxy_rotator()
        if rotator.enabled:
            proxy = rotator.get_rotating_proxy()
            attempts = 0
            while proxy is not None and attempts < 2:
                attempts += 1
                client = rotator.get_client(proxy=proxy)
                response = await client.get(url, headers={"User-Agent": "rmi-news/1.0"})
                if response.is_success and not _is_waf_block(
                    response.status_code, response.text[:_OG_HTML_MAX_CHARS]
                ):
                    return response.text[:_OG_HTML_MAX_CHARS]
                proxy = rotator.next_after_failure(proxy)
    except Exception:
        logger.debug("tg_news:proxy_pool_fetch_failed url=%s", url[:120], exc_info=True)

    # 3) FlareSolverr for WAF-blocked fetches.
    return await _fetch_via_flaresolverr(url)


def _normalize_image_url(raw: str, base_url: str) -> str | None:
    """Resolve protocol-relative / root-relative og:image URLs to absolute.

    2026-09-02 (image-leak fix): after resolving, the result is checked
    against the image doctrine's banned patterns (``is_valid_image``) — a
    scraped og:image that IS a social profile avatar/header (e.g. the
    operator's X header at pbs.twimg.com/profile_images/…) returns ``None``
    here, at the SOURCE, instead of leaking into a channel post.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        result = f"https:{raw}"
    elif raw.startswith("/"):
        from urllib.parse import urlsplit

        parts = urlsplit(base_url)
        result = f"{parts.scheme}://{parts.netloc}{raw}"
    else:
        result = raw
    if not is_valid_image(result):
        logger.debug("tg_news:og_image_banned url=%s", result[:120])
        return None
    return result


async def _fetch_og_image(url: str, timeout: float = _OG_IMAGE_TIMEOUT) -> str | None:
    """Best-effort og:image for a real article URL (fail-open → ``None``).

    Skips Telegram links (t.me pages have no meaningful story image) and
    never raises: network errors, timeouts, non-200s, and pages without an
    image meta tag all yield ``None``. Bound to *timeout* seconds for the
    direct attempt so a slow publisher never blocks the sweep; blocked pages
    escalate to the proxy ladder (FlareSolverr when needed).
    """
    if is_telegram_link(url):
        return None
    html = await _fetch_page(url, timeout)
    if not html:
        return None
    for pattern in _OG_IMAGE_RES:
        match = pattern.search(html)
        if match:
            normalized = _normalize_image_url(match.group(1), url)
            # 2026-09-02: belt-and-braces — the normalized URL is re-checked
            # here even though _normalize_image_url already filters, so a
            # future refactor of the normalizer cannot silently re-open the
            # profile-image leak.
            if normalized and not is_valid_image(normalized):
                return None
            return normalized
    return None


def _should_fetch_og_image(article: NewsArticle) -> bool:
    """True when the sweep should try the source page's og:image for *article*.

    Skips articles whose source URL is a t.me fallback (no meaningful story
    image) and articles that already carry a real media reference. The
    ``tg:`` marker that normalize sets for photo/video posts without a real
    media id is replaced when the source og:image resolves.
    """
    url = (article.source_url or "").strip()
    if not url or is_telegram_link(url):
        return False
    media = (article.media or "").strip()
    return not media or media.startswith("tg:")
