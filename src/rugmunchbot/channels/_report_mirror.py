"""Report mirror — feed-site + Ghost longform publish for the daily reports.

Shared by the Midday Market Report and the Evening Wrap (and available to
the Morning Brief). One canonical path so the three reports mirror
identically:

1. Mirror the report to the feed site (``mirror_post_to_feed``) → slug.
2. Transform the Telegram-HTML report to longform markdown
   (``channels._longform.to_markdown``) and publish it to Ghost via
   ``_vendor.content_platform.create_post``.

The Ghost step is best-effort and fails open: a broken vendor import, a
missing admin key, or a quality-gate rejection must never block the
Telegram post. The feed-site deep-link wins when both succeed.

The transform is the point: Ghost must receive markdown/lexical (H1/H2/H3,
real links, single-blank spacing), never Telegram ``<b>``/``<i>``/``<a>``
markup or ruled ``━━━`` banners.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from rugmunchbot.channels._longform import to_markdown

logger = logging.getLogger("rmi_bot.channels.report_mirror")

#: feed.rugmunch.io deep-link base for a mirrored post slug.
FEED_POST_URL_FMT = "https://rugmunch.io/blog/{slug}"

#: Full-report cap for the feed store. The old 3900-char Telegram cap cut
#: the commentary mid-sentence on the website (2026-10-24); the SPA renders
#: the whole body, so the store carries it.
FEED_TEXT_MAX = 12000


def _title(edition: str) -> str:
    """Dated Ghost post title for an edition."""
    return f"{edition} — {datetime.now(UTC).strftime('%B %d, %Y')}"


async def _mirror_to_feed_site(
    report: str, image_url: str, *, story_ref: str, title: str, tags: list[str]
) -> str:
    """Mirror to the feed site; return the deep-link ("" on failure)."""
    try:
        from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

        slug = (
            mirror_post_to_feed(
                {
                    "text": report[:FEED_TEXT_MAX],
                    "story_ref": story_ref,
                    "source": "rugmunch.io",
                    "kind": "briefing",
                    "title": title,
                    "tags": tags,
                    "image_url": image_url,
                }
            )
            or ""
        )
    except Exception as exc:
        logger.warning("report_mirror:feed_mirror_failed err=%s", str(exc)[:120])
        return ""
    return FEED_POST_URL_FMT.format(slug=slug) if slug else ""


async def _publish_ghost(report: str, image_url: str, *, title: str, tags: list[str]) -> str:
    """Transform to longform markdown and publish to Ghost; return URL ("" on failure)."""
    try:
        from rugmunchbot._vendor.content_platform import create_post

        markdown = to_markdown(report)
        ghost = await create_post(
            content=markdown,
            post_type="briefing",
            title=title,
            tags=tags,
            feature_image=image_url,
        )
    except Exception as exc:
        logger.warning("report_mirror:ghost_publish_failed err=%s", str(exc)[:120])
        return ""
    return str(ghost.get("url") or "") if isinstance(ghost, dict) else ""


async def mirror_report(
    report: str,
    image_url: str,
    *,
    edition: str,
    story_ref: str,
    tags: list[str],
    enabled: bool = True,
) -> str:
    """Mirror a report to the feed site + Ghost longform. Returns a deep-link.

    ``enabled`` gates the whole mirror (the per-report ``*_FEED_ENABLED``
    switch). The feed-site link wins when both succeed; the Ghost URL is the
    fallback. Returns "" when the mirror is disabled or both steps fail.
    """
    if not enabled:
        return ""
    title = _title(edition)
    feed_url = await _mirror_to_feed_site(
        report, image_url, story_ref=story_ref, title=title, tags=tags
    )
    ghost_url = await _publish_ghost(report, image_url, title=title, tags=tags)
    return feed_url or ghost_url


__all__ = ["FEED_POST_URL_FMT", "mirror_report"]
