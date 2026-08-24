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


# ── Scan report degen-intel rendering (production audit 2026-08-23) ─────
def test_bundle_verdict_derives_confidence_from_cluster_scores():
    from rugmunchbot.formatters._scan_report_common._layout import (
        _bundle_verdict_row,
    )

    r = {
        "bundle_analysis": {
            "is_bundled": True,
            "launch_cluster_score": 87,
            "common_funder_score": 74,
        }
    }
    row = _bundle_verdict_row(r)
    assert "87% conf" in row, "cluster scores must derive confidence"
    assert "HIGH" in row
    assert "unknown conf" not in row


def test_distribution_verdict_reads_top10_pct():
    from rugmunchbot.formatters._scan_report_common._layout import (
        _distribution_verdict,
    )

    r = {"holder_analysis": {"top10_pct": 61.2}}
    assert _distribution_verdict(r) == ("🔴", "CONCENTRATED")


def test_fresh_wallet_pct_reads_holder_analysis():
    from rugmunchbot.formatters._scan_report_common._fields import (
        _fresh_wallet_pct,
    )

    r = {"holder_analysis": {"new_wallets_pct": 41.2}}
    assert _fresh_wallet_pct(r) == 41.2


# ── Bundle enrichment + Gini fallback (production audit 2026-08-24) ────
def test_bundle_verdict_shows_cluster_details():
    from rugmunchbot.formatters._scan_report_common._layout import (
        _bundle_verdict_row,
    )

    r = {
        "bundle_analysis": {
            "is_bundled": True,
            "launch_cluster_score": 87,
            "common_funder_score": 74,
            "deployer_funded_pct": 38.5,
        }
    }
    row = _bundle_verdict_row(r)
    assert "launch 87" in row
    assert "funder 74" in row
    assert "deployer-funded 38%" in row


def test_bundle_verdict_clean_when_no_subscores():
    from rugmunchbot.formatters._scan_report_common._layout import (
        _bundle_verdict_row,
    )

    r = {"bundle_analysis": {"is_bundled": True}}
    row = _bundle_verdict_row(r)
    assert "unknown conf" in row  # honest when no data
