"""Content-pipeline importance scoring tests (production-coverage gap fix)."""

from rugmunchbot._vendor.news_ingest.models import NewsArticle
from rugmunchbot.channels.importance import (
    _heuristic_importance,
    _heuristic_scored,
)


def _art(title: str, body: str = "", source: str = "test") -> NewsArticle:
    return NewsArticle(
        title=title, body=body, source_channel=source, source_url="https://t.me/test/1"
    )


def test_strong_keywords_score_higher_than_plain():
    plain = _heuristic_importance(_art("Weekly roundup of notes"))
    hack = _heuristic_importance(
        _art("Exploit drains $12M from protocol", "hacker bridges stolen funds")
    )
    assert hack > plain, "security-critical story must outrank filler"


def test_scores_bounded_0_1():
    for title in ("", "a", "SEC sues exchange exploit hack rug drainer bridge"):
        s = _heuristic_importance(_art(title))
        assert 0.0 <= s <= 1.0


def test_heuristic_scored_marks_fallback():
    arts = [_art("t1"), _art("t2")]
    scored = _heuristic_scored(arts)
    assert len(scored) == 2
    assert all(s.fallback for s in scored)
    assert all(s.best_query == "heuristic-fallback" for s in scored)


# ── Title rewrite sanitizer ─────────────────────────────────────────────
def test_clean_rewritten_strips_wrappers():
    from rugmunchbot.channels.rewrite_titles import _clean_rewritten

    assert _clean_rewritten('"A Title"') == "A Title"
    assert _clean_rewritten("Title: something") in ("Title: something", "something")
    assert _clean_rewritten(123) == "123"  # coerces non-strings safely
