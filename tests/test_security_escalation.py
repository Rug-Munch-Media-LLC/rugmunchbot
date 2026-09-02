"""Security-tweet breaking-news escalation contract (2026-09-03).

A tweet from a monitored security account that mentions a security keyword
AND has high engagement (>50 likes or >20 retweets) escalates to the main
channel (@cryptorugmuncher) immediately — it must not wait for the daily
roundup. The escalation names the researcher, links the original tweet,
includes the key details, and tags it as breaking security news.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_rag


@pytest.fixture(autouse=True)
def _fresh_dedupe(monkeypatch):
    monkeypatch.setattr(_core, "_posted_keys", {})
    return


class _Recorder:
    def __init__(self, succeed: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.succeed = succeed

    async def __call__(self, bot: Any, chat_id: str, text: str) -> bool:
        self.calls.append((chat_id, text))
        return self.succeed


class _Bot:
    async def send_message(self, *a, **k):
        return True


class _Ctx:
    bot = _Bot()


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


# ── poster ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_posts_qualifying_tweet_to_main(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100main")
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)
    _patch_fetch(monkeypatch, [_tweet()])

    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 1
    assert len(sender.calls) == 1
    assert sender.calls[0][0] == "-100main"
    assert "ZachXBT" in sender.calls[0][1]


@pytest.mark.asyncio
async def test_skips_non_qualifying_tweet(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100main")
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)
    _patch_fetch(monkeypatch, [_tweet(like_count=5, retweet_count=2)])

    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 0
    assert sender.calls == []


@pytest.mark.asyncio
async def test_does_not_double_post_same_tweet(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100main")
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)
    _patch_fetch(monkeypatch, [_tweet()])

    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 1
    # Second tick sees the same tweet → deduped to zero.
    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 0
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_releases_dedupe_on_failed_send(monkeypatch):
    sender = _Recorder(succeed=False)
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100main")
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)
    _patch_fetch(monkeypatch, [_tweet()])

    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 0
    # Failed send released the mark → the next tick retries.
    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 0
    assert len(sender.calls) == 2


@pytest.mark.asyncio
async def test_noop_without_main_chat_id(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: None)
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)
    _patch_fetch(monkeypatch, [_tweet()])

    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 0
    assert sender.calls == []


@pytest.mark.asyncio
async def test_fails_open_on_fetch_error(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100main")
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)

    async def _boom():
        raise RuntimeError("getxapi down")

    _patch_fetch(monkeypatch, _boom)

    assert await _posts_rag.post_security_tweet_escalation(_Ctx()) == 0
    assert sender.calls == []


def _patch_fetch(monkeypatch, tweets):
    """Patch the lazy-imported fetch_security_tweets in tools.x_ingest.

    ``post_security_tweet_escalation`` imports ``fetch_security_tweets``
    lazily inside the function body, so it is not a module attribute of
    ``_posts_rag`` to monkeypatch — patch the source module instead.
    """
    import rugmunchbot.tools.x_ingest as x_ingest

    if callable(tweets):
        monkeypatch.setattr(x_ingest, "fetch_security_tweets", tweets)
    else:

        async def _fetch() -> list[dict[str, Any]]:
            return [dict(t) for t in tweets]

        monkeypatch.setattr(x_ingest, "fetch_security_tweets", _fetch)
