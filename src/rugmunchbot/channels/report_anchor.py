"""Report anchor — the 3-unique-posts/day contract (2026-10-17).

Operator doctrine: **three unique posts per day** on @cryptorugmuncher and
the feed, each a DIFFERENT facet, no duplicated content:

* ``morning``  — The Daily Rug (Morning Market Brief)  · crypto stories
* ``midday``   — Midday Market Report                  · macro + broader markets
* ``evening``  — Evening Wrap                          · scams, shills, rugs

These three are the ANCHOR. Every other main-channel post type (breaking
news, alerts, quality, RAG drafts) is ADDITIVE and must not duplicate an
anchor's content. The anchor is enforced two ways:

1. **Exactly three fire daily** — :func:`anchor_editions` is the single
   source of truth for which editions exist and their post types; the
   scheduler registers one ``run_daily`` job per edition, and
   :func:`assert_anchor_contract` fails loudly if the set drifts.
2. **No story appears in two editions** — the three-way dedup in
   :mod:`rugmunchbot.channels._edition_dedup` (mark on post, exclude prior
   on fetch). This module adds the missing half: the ADDITIVE lanes
   (news / alerts / RAG) call :func:`exclude_report_stories` so a story the
   anchor already carried is never re-posted as breaking news.

Fail-open everywhere: a Redis outage must never silence the anchor or the
news lane.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rugmunchbot.channels._edition_dedup import (
    EDITIONS,
    story_hash,
)

logger = logging.getLogger("rmi_bot.channels.report_anchor")

#: The three anchor editions, in publication order, with their post type and
#: the graphic key each resolves through (``support.post_graphics``).
ANCHOR_EDITIONS: tuple[tuple[str, str], ...] = (
    ("morning", "daily_rug"),
    ("midday", "midday_report"),
    ("evening", "evening_wrap"),
)

#: The exact number of anchor posts per day (the operator's 3/day law).
ANCHOR_POSTS_PER_DAY = 3


@dataclass(frozen=True)
class AnchorEdition:
    """One anchor edition: its dedup name, post type and graphic key."""

    edition: str
    post_type: str


def anchor_editions() -> tuple[AnchorEdition, ...]:
    """The three anchor editions as :class:`AnchorEdition` records."""
    return tuple(AnchorEdition(edition=e, post_type=p) for e, p in ANCHOR_EDITIONS)


def anchor_post_types() -> tuple[str, ...]:
    """The post types that constitute the daily anchor (exactly three)."""
    return tuple(p for _, p in ANCHOR_EDITIONS)


def assert_anchor_contract() -> None:
    """Fail loudly if the anchor drifts from the 3-unique-posts contract.

    Guards the invariant the operator set: exactly three editions, each a
    distinct post type, matching the dedup module's ordered edition list.
    Raises :class:`AssertionError` on drift — a silent fourth edition or a
    collapsed post type is a contract violation, not a warning.
    """
    editions = tuple(e for e, _ in ANCHOR_EDITIONS)
    post_types = anchor_post_types()
    assert len(ANCHOR_EDITIONS) == ANCHOR_POSTS_PER_DAY, (
        f"anchor must be exactly {ANCHOR_POSTS_PER_DAY} editions, got {len(ANCHOR_EDITIONS)}"
    )
    assert len(set(editions)) == len(editions), f"anchor editions must be unique: {editions}"
    assert len(set(post_types)) == len(post_types), (
        f"anchor post types must be distinct: {post_types}"
    )
    assert editions == EDITIONS, f"anchor editions {editions} must match dedup EDITIONS {EDITIONS}"


def exclude_report_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop stories already carried by ANY anchor edition today (fail-open).

    The additive lanes (breaking news, alerts, RAG drafts) call this before
    posting so a story the anchor already covered is never re-posted as
    breaking. Consults every edition's posted set for the current UTC day —
    the anchor is the day's canonical coverage, so it wins the story.

    Fail-open: a Redis outage returns *stories* unchanged (the news lane
    keeps flowing; the anchor's own three-way dedup still holds).
    """
    if not stories:
        return stories
    try:
        kept = _exclude_all_editions(stories)
        dropped = len(stories) - len(kept)
        if dropped:
            logger.info("report_anchor:excluded_from_additive dropped=%d", dropped)
        return kept
    except Exception as exc:  # fail-open — never block the additive lane
        logger.warning("report_anchor:exclude_failed err=%s", str(exc)[:140])
        return stories


def _exclude_all_editions(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Consult every anchor edition's posted set (fail-open)."""
    from rugmunchbot.channels._edition_dedup import _client, posted_key

    r = _client()
    if r is None:
        return stories
    posted: set[str] = set()
    for edition in EDITIONS:
        key = posted_key(edition)
        try:
            if not r.exists(key):
                continue
            posted |= {h.decode() if isinstance(h, bytes) else str(h) for h in r.smembers(key)}
        except Exception as exc:  # fail-open per edition
            logger.debug(
                "report_anchor:edition_read_failed edition=%s err=%s", edition, str(exc)[:80]
            )
    if not posted:
        return stories
    return [s for s in stories if story_hash(s) not in posted]


def filter_scored_articles(scored: list[Any], to_story: Any) -> list[Any]:
    """Drop scored articles whose story an anchor edition already carried.

    The additive news lane calls this before selecting a main-channel post:
    a story the day's anchor already covered must not re-post as breaking.
    ``to_story`` converts a scored article to a story dict (the caller owns
    that shape). Fail-open: any error keeps the full list — the anchor's own
    three-way dedup still holds.
    """
    try:
        stories = [to_story(s) for s in scored]
        kept = exclude_report_stories(stories)
        if len(kept) == len(stories):
            return scored
        kept_hashes = {story_hash(s) for s in kept}
        return [s for s in scored if story_hash(to_story(s)) in kept_hashes]
    except Exception as exc:  # fail-open — never block the news lane
        logger.debug("report_anchor:filter_scored_skipped err=%s", str(exc)[:120])
        return scored


__all__ = [
    "ANCHOR_EDITIONS",
    "ANCHOR_POSTS_PER_DAY",
    "AnchorEdition",
    "anchor_editions",
    "anchor_post_types",
    "assert_anchor_contract",
    "exclude_report_stories",
    "filter_scored_articles",
]
