"""Image doctrine enforcement — EVERY channel post carries an image.

Policy (2026-08-31): no naked text posts on any RugMunch channel
(@cryptorugmuncher, @rmicryptonews, @rmiscans, @rmialpha, @rmialerts,
@rmiupdates). Resolution order for a post's image:

1. An image the content already carries (``image_url`` / ``image``).
2. News posts: fetch ``og:image`` from the source URL (reuses the proven
   ``telegram_news._sources._fetch_og_image`` extractor — no new deps).
3. Scanner / scam / whale / digest posts: the branded RugMunch placeholder
   (``quality_gate.PLACEHOLDER_IMAGE_URL``) so ``_post_photo_aware`` always
   attaches a photo.

Pure fail-open: any resolution failure falls through to the placeholder.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.quality_gate import PLACEHOLDER_IMAGE_URL

__all__ = ["PLACEHOLDER_IMAGE_URL", "ensure_post_image", "resolve_post_image"]

#: URL fragments that identify SOCIAL PROFILE imagery (avatars / channel
#: headers), never post content. 2026-09-01: the @cryptorugmuncher X header
#: (pbs.twimg.com/profile_images/…) shipped as a post image because a
#: scraped story item carried its source profile's header as ``image_url``.
#: These are identity chrome, not content — always reject.
_BANNED_IMAGE_PATTERNS = (
    "pbs.twimg.com/profile_images",
    "/brand/banner.jpg",  # operator's personal X header
    "twimg.com/profile_images",
)


def _valid_image(url: object) -> str:
    value = str(url or "").strip()
    if not value.startswith(("http://", "https://")):
        return ""
    lowered = value.lower()
    if any(pattern in lowered for pattern in _BANNED_IMAGE_PATTERNS):
        return ""
    return value


def ensure_post_image(post: Any) -> Any:
    """Guarantee a ``NewsPost`` carries an image for ``_post_photo_aware``.

    ``NewsPost`` is a ``str`` subclass with an ``image_url`` attribute;
    ``_post_photo_aware`` reads it. Plain ``str`` posts are wrapped in a
    ``NewsPost`` carrying the branded placeholder (image doctrine: no
    naked text posts). Returns the post so callers can wrap formatter
    output inline: ``ensure_post_image(format_scan_card(item))``.
    """
    if isinstance(post, str) and not isinstance(post, _news_post_cls()):
        wrapped = _news_post_cls()(post)
        wrapped.image_url = PLACEHOLDER_IMAGE_URL
        return wrapped
    image_url = getattr(post, "image_url", None)
    if image_url is None:
        return post
    if not _valid_image(image_url):
        post.image_url = PLACEHOLDER_IMAGE_URL
    return post


def _news_post_cls() -> type:
    """Lazy ``NewsPost`` import (avoids a channels → support cycle)."""
    from rugmunchbot.channels.formatters._primitives import NewsPost

    return NewsPost


async def resolve_post_image(
    kind: str,
    *,
    image_url: object = "",
    source_url: object = "",
    logger: Any = None,
) -> str:
    """Best image for a post of *kind* — never returns ``""``.

    ``kind`` is the logical channel ("main", "news", "scans", "alpha",
    "alerts", "updates") or a post kind ("scanner_alert", …). News-ish
    kinds get an og:image fetch from *source_url* when no image is
    attached; everything else lands on the branded placeholder.

    2026-09-01 (always-real-bytes doctrine): a resolved og:image URL is
    rewritten through the backend thumbnail proxy
    (``/api/v1/media/thumb?url=…``) — publishers hotlink-block Telegram's
    fetcher, so the raw og:image URL alone still yields a text-only post.
    The proxy fetches server-side (direct → residential ladder), caches in
    Redis 24h, and always serves image bytes. The placeholder is the LAST
    resort only.
    """
    carried = _valid_image(image_url)
    if carried:
        return _thumb_proxy(carried, logger=logger)
    if kind in ("main", "news") and _valid_image(source_url):
        try:
            from rugmunchbot.channels.telegram_news._sources import _fetch_og_image

            og = _valid_image(await _fetch_og_image(str(source_url).strip()))
            if og:
                return _thumb_proxy(og, logger=logger)
        except Exception as exc:  # fail-open — placeholder below
            if logger is not None:
                logger.debug("image_policy_og_failed err=%s", str(exc)[:120])
    return PLACEHOLDER_IMAGE_URL


def _thumb_proxy(image_url: str, *, logger: Any = None) -> str:
    """Wrap a remote image URL in the backend thumb proxy (fail-open).

    Telegram must fetch the image bytes itself; hotlink-blocked hosts make
    that fail. ``GET /api/v1/media/thumb?url=`` fetches server-side and
    serves the bytes with long-lived cache headers. Same-host images (the
    branded placeholder) pass through unwrapped; if the backend base can't
    be resolved the ORIGINAL url is returned — a direct fetch attempt still
    beats skipping the photo.
    """
    try:
        from urllib.parse import quote, urlsplit

        if urlsplit(image_url).hostname in ("rugmunch.io", "www.rugmunch.io"):
            return image_url  # already ours — no proxy needed
        from rugmunchbot.config import BACKEND_URL

        return f"{BACKEND_URL}/api/v1/media/thumb?url={quote(image_url, safe='')}"
    except Exception as exc:
        if logger is not None:
            logger.debug("thumb_proxy_wrap_failed err=%s", str(exc)[:120])
        return image_url
