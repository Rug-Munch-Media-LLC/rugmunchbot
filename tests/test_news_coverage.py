"""tg_news coverage tests — the 2026-10-23 "news not applied" regressions.

Three defects, each caught here:

1. ``route_news`` applied the rerank-calibrated 0.4 news floor to HEURISTIC
   fallback scores, so a reranker outage silently skipped every ordinary
   headline (17 fresh stories swept, 0 posted).
2. ``score_importance`` scored only ``articles[:MAX_ARTICLES_PER_BATCH]`` —
   every story past #100 was persisted and RAG-embedded but could never be
   routed or posted.
3. Nothing reported the funnel per source, so a channel that never reached
   content was invisible in the logs.
"""

from __future__ import annotations

import pytest

from rugmunchbot._vendor.news_ingest.models import NewsArticle
from rugmunchbot.channels import importance
from rugmunchbot.channels.importance import ScoredArticle, score_importance
from rugmunchbot.channels.router import (
    ROUTE_NEWS_MIN_IMPORTANCE,
    ROUTE_NEWS_MIN_IMPORTANCE_FALLBACK,
    route_news,
)
from rugmunchbot.channels.telegram_news import _coverage


def _art(title: str, source: str = "cointelegraph", body: str = "body") -> NewsArticle:
    return NewsArticle(
        title=title,
        body=body,
        source_channel=source,
        source_url=f"https://t.me/{source}/1",
    )


# ── 1. fallback routing ──────────────────────────────────────────────────


def test_fallback_floor_is_lower_than_rerank_floor() -> None:
    assert ROUTE_NEWS_MIN_IMPORTANCE_FALLBACK < ROUTE_NEWS_MIN_IMPORTANCE


def test_heuristic_score_below_rerank_floor_still_routes_to_news_in_fallback() -> None:
    # 0.3 is a typical heuristic score (1 strong keyword) — it must post when
    # the reranker is down instead of being silently skipped.
    assert route_news(0.3, "news", fallback=True) == "news"
    assert route_news(0.3, "news") == "skip"


def test_fallback_keeps_main_gate_escalation() -> None:
    assert route_news(0.9, "news", fallback=True) == "main"


def test_fallback_does_not_soften_scam_danger_intel() -> None:
    assert route_news(0.1, "scam", fallback=True) == "skip"
    assert route_news(0.5, "scam", fallback=True) == "alerts"


def test_fallback_keeps_alpha_channel_separation() -> None:
    assert route_news(0.1, "alpha", fallback=True) == "alpha"


# ── 2. every article is scored ───────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importance, "_consecutive_reranker_failures", 0)
    monkeypatch.setattr(importance, "_reranker_cooldown_until", 0.0)


async def test_score_importance_scores_past_the_batch_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importance, "MAX_ARTICLES_PER_BATCH", 2)

    async def _fake_rerank(query: str, texts: list[str]) -> dict[int, float]:
        return {i: 0.5 for i in range(len(texts))}

    monkeypatch.setattr(importance, "_rerank_query", _fake_rerank)
    articles = [_art(f"story {i}") for i in range(5)]
    scored = await score_importance(articles)

    assert len(scored) == 5, "items past MAX_ARTICLES_PER_BATCH must not be dropped"
    assert [s.article.title for s in scored] == [a.title for a in articles]
    assert all(not s.fallback for s in scored)


async def test_breaker_counts_once_per_call_not_per_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importance, "MAX_ARTICLES_PER_BATCH", 2)

    async def _dead_rerank(query: str, texts: list[str]) -> dict[int, float]:
        return {}

    monkeypatch.setattr(importance, "_rerank_query", _dead_rerank)
    scored = await score_importance([_art(f"s{i}") for i in range(5)])

    assert len(scored) == 5
    assert all(s.fallback for s in scored)
    assert importance._consecutive_reranker_failures == 1


# ── 3. per-source funnel ────────────────────────────────────────────────


def test_coverage_reports_every_source_including_silent_ones() -> None:
    articles = [
        _art("a1", "coindesk"),
        _art("a2", "coindesk"),
        _art("a3", "quietchannel"),
    ]
    scored = [ScoredArticle(article=a, importance=0.5) for a in articles]
    routed = {
        _coverage.article_key(articles[0]): "news",
        _coverage.article_key(articles[1]): "skip",
        _coverage.article_key(articles[2]): "news",
    }
    posted = {_coverage.article_key(articles[0])}

    cov = _coverage.news_coverage(articles, scored, routed, posted)

    assert cov["coindesk"] == {"swept": 2, "scored": 2, "routed": 1, "posted": 1}
    assert cov["quietchannel"] == {"swept": 1, "scored": 1, "routed": 1, "posted": 0}
    assert _coverage.coverage_totals(cov) == {
        "swept": 3,
        "scored": 3,
        "routed": 2,
        "posted": 1,
    }


def test_coverage_skips_count_as_swept_not_routed() -> None:
    article = _art("buried story", "pakistannews")
    routed = {_coverage.article_key(article): "skip"}
    cov = _coverage.news_coverage([article], [], routed, set())
    assert cov["pakistannews"]["swept"] == 1
    assert cov["pakistannews"]["routed"] == 0
    assert cov["pakistannews"]["posted"] == 0


def test_coverage_handles_missing_source_and_empty_input() -> None:
    orphan = _art("orphan", "")
    cov = _coverage.news_coverage([orphan])
    assert _coverage.UNKNOWN_SOURCE in cov
    assert _coverage.news_coverage([]) == {}
    assert _coverage.log_news_coverage([], None, None, None) == {}
