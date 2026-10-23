"""Per-source funnel accounting for the tg_news lane (why a story never posted).

The sweep persists and RAG-embeds FAR more articles than it posts, and until
now nothing reported the gap per source: the tick envelope logged
``items_swept=0 items_scored=0`` for ``channel_tg_news`` and the only drop
traces were ad-hoc lines (``cross_run_dedup``, ``ledger_blocked``). An
operator asking "did @coinfox's story reach the channel?" had no answer.

This module is the accounting layer only — pure functions over data the
scheduler already holds (no network, no Redis, no Telegram):

    swept → scored → routed(eligible per destination) → posted

Counts are keyed by ``article.source_channel`` so every channel is visible
even when it contributed nothing (a channel that sweeps 40 items and posts 0
is exactly the defect class this exists to surface).

Used by ``telegram_news._scheduler.post_tg_news_update``; the emitted
``tg_news:coverage`` line is the operator's per-source proof of coverage.
"""

from __future__ import annotations

from typing import Any, Final

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.tg_news")

#: Funnel stages, in pipeline order.
FUNNEL_STAGES: Final[tuple[str, ...]] = ("swept", "scored", "routed", "posted")

#: Source label for an article with no ``source_channel``.
UNKNOWN_SOURCE: Final[str] = "unknown"


def article_source(article: Any) -> str:
    """Source-channel label for *article* (never empty)."""
    raw = str(getattr(article, "source_channel", "") or "").strip().lstrip("@")
    return raw or UNKNOWN_SOURCE


def article_key(article: Any) -> str:
    """Stable identity for one article — ``source|title``, lowercased.

    Matches how the lane dedupes (canonical-URL-first, normalized title
    second); the title is the only key the scheduler holds after routing.
    """
    title = str(getattr(article, "title", "") or "").strip().lower()
    return f"{article_source(article)}|{title}"


def _empty_row() -> dict[str, int]:
    return dict.fromkeys(FUNNEL_STAGES, 0)


def news_coverage(
    articles: list[Any],
    scored: list[Any] | None = None,
    routed: dict[str, str] | None = None,
    posted: set[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Per-source funnel counts for one sweep.

    ``scored`` items are ``ScoredArticle`` objects (``.article``); ``routed``
    maps :func:`article_key` → destination (``"skip"`` counts as swept but
    NOT routed — the item was eligible for nothing); ``posted`` is the set of
    :func:`article_key` values actually published. Pure, never raises.
    """
    cov: dict[str, dict[str, int]] = {}
    for article in articles or []:
        cov.setdefault(article_source(article), _empty_row())["swept"] += 1
    for item in scored or []:
        article = getattr(item, "article", None)
        if article is None:
            continue
        cov.setdefault(article_source(article), _empty_row())["scored"] += 1
    for key, dest in (routed or {}).items():
        if not dest or dest == "skip":
            continue
        source = key.split("|", 1)[0] or UNKNOWN_SOURCE
        cov.setdefault(source, _empty_row())["routed"] += 1
    for key in posted or ():
        source = key.split("|", 1)[0] or UNKNOWN_SOURCE
        cov.setdefault(source, _empty_row())["posted"] += 1
    return cov


def coverage_totals(cov: dict[str, dict[str, int]]) -> dict[str, int]:
    """Sum the funnel across every source (one row per stage)."""
    totals = _empty_row()
    for row in cov.values():
        for stage in FUNNEL_STAGES:
            totals[stage] += int(row.get(stage, 0))
    return totals


def log_news_coverage(
    articles: list[Any],
    scored: list[Any] | None = None,
    routed: dict[str, str] | None = None,
    posted: set[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Compute the funnel and emit ``tg_news:coverage`` + totals.

    The per-source line lists ``source:swept/scored/routed/posted`` for every
    channel seen this sweep, sorted worst-covered first (swept but unrouted
    ahead of covered ones) so the leak is the first thing in the log line.
    Returns the coverage map. Never raises.
    """
    try:
        cov = news_coverage(articles, scored, routed, posted)
        totals = coverage_totals(cov)
        ordered = sorted(
            cov.items(),
            key=lambda kv: (kv[1]["posted"], kv[1]["routed"], -kv[1]["swept"]),
        )
        logger.info(
            "tg_news:coverage sources=%s swept=%s scored=%s routed=%s posted=%s",
            " ".join(
                f"{src}:{row['swept']}/{row['scored']}/{row['routed']}/{row['posted']}"
                for src, row in ordered
            ),
            totals["swept"],
            totals["scored"],
            totals["routed"],
            totals["posted"],
        )
        return cov
    except Exception:  # accounting must never break publishing
        logger.warning("tg_news:coverage_failed", exc_info=True)
        return {}


__all__ = [
    "FUNNEL_STAGES",
    "UNKNOWN_SOURCE",
    "article_key",
    "article_source",
    "coverage_totals",
    "log_news_coverage",
    "news_coverage",
]
