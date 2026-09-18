"""Security-tweet breaking-news escalation contract (2026-09-03).

A tweet from a monitored security account that mentions a security keyword
AND has high engagement (>50 likes or >20 retweets) escalates to the main
channel (@cryptorugmuncher) immediately — it must not wait for the daily
roundup. The escalation names the researcher, links the original tweet,
includes the key details, and tags it as breaking security news.

2026-09-18: X ingestion (fetch/normalize/embed/ingest) moved to **rugbrain**
(the RAG service). The bot's ``post_security_tweet_escalation`` is now a
fail-open no-op; the pure predicate/formatter are retained for the RAG-read
path and remain under test here.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _posts_rag


class _Ctx:
    """Minimal context — the retired poster must not touch it."""


def _tweet(**overrides: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "tweet_id": "1234567890",
        "handle": "zachxbt",
        "author_name": "ZachXBT",
        "text": (
            "A protocol was just exploited and funds were drained. "
            "Investigating the bridge. https://x.com/zachxbt/status/1234567890"
        ),
        "url": "https://x.com/zachxbt/status/1234567890",
        "like_count": 120,
        "retweet_count": 45,
        "reply_count": 10,
        "quote_count": 3,
        "created_at": "2026-09-03T10:00:00Z",
        "source": "getxapi",
    }
    d.update(overrides)
    return d


# ── pure predicate ──────────────────────────────────────────────────────


def test_matches_qualifying_security_tweet():
    assert _posts_rag._security_tweet_matches(_tweet()) is True


@pytest.mark.parametrize(
    "handle",
    ["zachxbt", "peckshield", "blockaid", "certikalert", "slowmist", "beosin"],
)
def test_matches_all_monitored_accounts(handle):
    assert _posts_rag._security_tweet_matches(_tweet(handle=handle)) is True


def test_rejects_non_monitored_account():
    assert _posts_rag._security_tweet_matches(_tweet(handle="vitalikbuterin")) is False


def test_rejects_tweet_without_security_keyword():
    tweet = _tweet(text="Great thread on the latest market trends and alpha.")
    assert _posts_rag._security_tweet_matches(tweet) is False


@pytest.mark.parametrize(
    "keyword",
    ["exploit", "hack", "drained", "rug", "scam", "flash loan", "bridge"],
)
def test_matches_each_security_keyword(keyword):
    tweet = _tweet(text=f"Breaking: {keyword} detected on a major protocol.")
    assert _posts_rag._security_tweet_matches(tweet) is True


def test_keyword_match_is_case_insensitive():
    tweet = _tweet(text="EXPLOIT in progress on the vault.")
    assert _posts_rag._security_tweet_matches(tweet) is True


def test_rejects_low_engagement():
    tweet = _tweet(like_count=10, retweet_count=5)
    assert _posts_rag._security_tweet_matches(tweet) is False


def test_accepts_high_likes_only():
    tweet = _tweet(like_count=200, retweet_count=5)
    assert _posts_rag._security_tweet_matches(tweet) is True


def test_accepts_high_retweets_only():
    tweet = _tweet(like_count=10, retweet_count=50)
    assert _posts_rag._security_tweet_matches(tweet) is True


def test_engagement_boundary_is_strictly_greater():
    # Exactly 50 likes and 20 retweets is NOT high engagement (must be >).
    tweet = _tweet(like_count=50, retweet_count=20)
    assert _posts_rag._security_tweet_matches(tweet) is False


def test_malformed_metrics_fail_open_to_zero():
    tweet = _tweet(like_count="n/a", retweet_count=None)
    assert _posts_rag._security_tweet_matches(tweet) is False


# ── formatter ───────────────────────────────────────────────────────────


def test_formatter_names_researcher_and_links_tweet():
    text = _posts_rag.format_security_tweet_escalation(_tweet())
    assert "ZachXBT" in text
    assert "flagged a potential exploit" in text
    assert "https://x.com/zachxbt/status/1234567890" in text
    assert "120 likes" in text
    assert "45 retweets" in text


def test_formatter_escapes_html_in_tweet_text():
    tweet = _tweet(text="<script>alert('xss')</script> exploit detected")
    text = _posts_rag.format_security_tweet_escalation(tweet)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_formatter_truncates_long_tweet():
    tweet = _tweet(text="x" * 500)
    text = _posts_rag.format_security_tweet_escalation(tweet)
    assert "…" in text


# ── retired poster (X ingestion owned by rugbrain) ──────────────────────


@pytest.mark.asyncio
async def test_poster_is_retired_noop():
    """The bot no longer fetches X security tweets — poster returns 0.

    X ingestion moved to rugbrain; the bot consumes X-derived intel through
    the RAG HTTP API. The poster must stay a fail-open no-op so the scheduler
    registration remains stable.
    """
    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 0
