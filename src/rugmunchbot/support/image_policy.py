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


def _valid_image(url: object) -> str:
    value = str(url or "").strip()
    return value if value.startswith(("http://", "https://")) else ""


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
    """
    carried = _valid_image(image_url)
    if carried:
        return carried
    if kind in ("main", "news") and _valid_image(source_url):
        try:
            from rugmunchbot.channels.telegram_news._sources import _fetch_og_image

            og = _valid_image(await _fetch_og_image(str(source_url).strip()))
            if og:
                return og
        except Exception as exc:  # fail-open — placeholder below
            if logger is not None:
                logger.debug("image_policy_og_failed err=%s", str(exc)[:120])
    return PLACEHOLDER_IMAGE_URL
