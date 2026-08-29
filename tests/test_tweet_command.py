"""/tweet + /tweets command and read_tweet/story_fetch X wiring tests.

Covers the X-post reader contract:

* ``tools.read_tweet.parse_target`` — URL / numeric id / @handle classification
* ``commands.tweet.format_tweet`` / ``format_tweet_list`` — safe HTML cards
* ``cmd_tweet`` fail-open paths — usage, no key, budget spent, provisioning
  (fetch → None) — the command NEVER scrapes and NEVER fabricates content
* ``read_tweet`` tool fail-open — no key
* ``story_fetch`` X guard — x.com URLs route to ApiPass, never the crawler
"""

from __future__ import annotations

import asyncio

from rugmunchbot.commands.tweet import cmd_tweet, format_tweet, format_tweet_list
from rugmunchbot.tools import apipass_x as ax, story_fetch as sf
from rugmunchbot.tools.read_tweet import parse_target, read_tweet

# ══════════════════════════════════════════════════════════════════════════
# parse_target (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_parse_target_x_urls():
    assert parse_target("https://x.com/foo/status/123456789") == {"tweet_id": "123456789"}
    assert parse_target("https://twitter.com/foo/status/42") == {"tweet_id": "42"}
    assert parse_target("https://x.com/i/status/777") == {"tweet_id": "777"}
    assert parse_target("check https://x.com/a/status/999 pls") == {"tweet_id": "999"}


def test_parse_target_numeric_id():
    assert parse_target("123456789012345678") == {"tweet_id": "123456789012345678"}
    assert parse_target("123") == {}  # too short — not a tweet id


def test_parse_target_handles():
    assert parse_target("@cryptowhale") == {"handle": "cryptowhale"}
    assert parse_target("cryptowhale") == {"handle": "cryptowhale"}


def test_parse_target_rejects_non_x():
    assert parse_target("") == {}
    assert parse_target("https://example.com/foo") == {}
    assert parse_target("https://x.com/foo") == {}  # profile page, not a post


# ══════════════════════════════════════════════════════════════════════════
# Formatters (pure)
# ══════════════════════════════════════════════════════════════════════════


def test_format_tweet_escapes_html_and_shows_metrics():
    t = {
        "tweet_id": "1",
        "text": "<script>alert(1)</script> $MOON is a gem",
        "handle": "k",
        "author_name": "KOL One",
        "like_count": 1234,
        "retweet_count": 56,
        "reply_count": 7,
        "created_at": "2026-08-29T10:00:00Z",
        "url": "https://x.com/k/status/1",
        "source": "apipass_x",
    }
    out = format_tweet(t)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "@k" in out
    assert "👍 1,234" in out
    assert "🔁 56" in out
    assert "💬 7" in out
    assert "https://x.com/k/status/1" in out


def test_format_tweet_list_compact():
    tweets = [
        {
            "tweet_id": "1",
            "text": "first call $PEPE long\nnext line",
            "handle": "k",
            "like_count": 10,
            "retweet_count": 2,
            "url": "https://x.com/k/status/1",
        },
        {
            "tweet_id": "2",
            "text": "second",
            "handle": "k",
            "like_count": 0,
            "retweet_count": 0,
            "url": "https://x.com/k/status/2",
        },
    ]
    out = format_tweet_list(tweets, "k", limit=5)
    assert "Recent posts from @k" in out
    assert "first call $PEPE long next line" in out  # newline flattened
    assert "👍10 🔁2" in out


# ══════════════════════════════════════════════════════════════════════════
# cmd_tweet fail-open paths (never scrapes, never fabricates)
# ══════════════════════════════════════════════════════════════════════════


class _FakeChat:
    async def send_action(self, action):
        pass


class _FakeMessage:
    def __init__(self):
        self.chat = _FakeChat()
        self.replies: list[tuple[str, dict]] = []

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return None


class _FakeUpdate:
    def __init__(self, message):
        self.effective_message = message


class _FakeContext:
    def __init__(self, args):
        self.args = args


async def _run_cmd(args: list[str], monkeypatch) -> str:
    msg = _FakeMessage()
    await cmd_tweet(_FakeUpdate(msg), _FakeContext(args))
    assert msg.replies, "cmd_tweet must reply"
    return msg.replies[-1][0]


def test_cmd_tweet_no_args_usage():
    async def _go():
        msg = _FakeMessage()
        await cmd_tweet(_FakeUpdate(msg), _FakeContext([]))
        assert msg.replies
        text = msg.replies[-1][0]
        assert "Usage" in text
        assert "/tweet" in text

    asyncio.run(_go())


def test_cmd_tweet_unparseable_input():
    async def _go():
        msg = _FakeMessage()
        await cmd_tweet(_FakeUpdate(msg), _FakeContext(["https://example.com/foo"]))
        text = msg.replies[-1][0]
        assert "Couldn't parse" in text

    asyncio.run(_go())


def test_cmd_tweet_no_key_fails_open(monkeypatch):
    monkeypatch.setattr(ax, "api_key_configured", lambda: False)
    out = asyncio.run(_run_cmd(["https://x.com/foo/status/123"], monkeypatch))
    assert "No ApiPass key" in out
    assert "never scrapes" in out


def test_cmd_tweet_budget_spent_fails_open(monkeypatch):
    monkeypatch.setattr(ax, "api_key_configured", lambda: True)
    monkeypatch.setattr(ax, "task_creates_remaining", lambda: 0)
    out = asyncio.run(_run_cmd(["https://x.com/foo/status/123"], monkeypatch))
    assert "hourly budget" in out


def test_cmd_tweet_provisioning_note_when_fetch_returns_none(monkeypatch):
    monkeypatch.setattr(ax, "api_key_configured", lambda: True)
    monkeypatch.setattr(ax, "task_creates_remaining", lambda: 5)

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(ax, "fetch_tweet_detail", _none)
    out = asyncio.run(_run_cmd(["https://x.com/foo/status/123"], monkeypatch))
    assert "ApiPass" in out
    assert "never scrapes X directly" in out


def test_cmd_tweet_handle_mode_provisioning_note(monkeypatch):
    monkeypatch.setattr(ax, "api_key_configured", lambda: True)
    monkeypatch.setattr(ax, "task_creates_remaining", lambda: 5)

    async def _none(*a, **k):
        return []

    monkeypatch.setattr(ax, "fetch_user_tweets", _none)
    out = asyncio.run(_run_cmd(["@cryptowhale"], monkeypatch))
    assert "ApiPass" in out


# ══════════════════════════════════════════════════════════════════════════
# read_tweet tool (chat-loop twin) — fail-open
# ══════════════════════════════════════════════════════════════════════════


def test_read_tweet_no_key_fails_open(monkeypatch):
    monkeypatch.setattr(ax, "api_key_configured", lambda: False)
    res = asyncio.run(read_tweet(url="https://x.com/foo/status/123"))
    assert not res.ok
    assert "no apipass key" in res.error.lower()
    assert res.source == "apipass_x"


def test_read_tweet_requires_target():
    res = asyncio.run(read_tweet())
    assert not res.ok
    assert "required" in res.error


def test_read_tweet_provisioning_note_when_fetch_returns_none(monkeypatch):
    monkeypatch.setattr(ax, "api_key_configured", lambda: True)

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(ax, "fetch_tweet_detail", _none)
    res = asyncio.run(read_tweet(tweet_id="123456789"))
    assert not res.ok
    assert "never scrapes" in res.error


# ══════════════════════════════════════════════════════════════════════════
# story_fetch X guard — x.com URLs NEVER reach the generic crawler
# ══════════════════════════════════════════════════════════════════════════


def test_story_fetch_x_url_no_key_never_crawls(monkeypatch):
    """Without a key the guard refuses BEFORE any crawler import/call."""
    monkeypatch.setattr(ax, "api_key_configured", lambda: False)
    res = asyncio.run(sf.story_fetch(url="https://x.com/foo/status/123"))
    assert not res.ok
    assert "scraping of X is disabled" in res.error
    assert res.data.get("source") == "apipass_x"


def test_story_fetch_x_url_routes_to_apipass(monkeypatch):
    """With a key + working ApiPass, the X post returns tweet payload, not HTML."""
    monkeypatch.setattr(ax, "api_key_configured", lambda: True)

    async def _fake_detail(tid):
        return {
            "tweet_id": tid,
            "text": "$MOON is the call",
            "handle": "kolo",
            "author_name": "KOL",
            "like_count": 9,
            "url": f"https://x.com/kolo/status/{tid}",
            "source": "apipass_x",
        }

    monkeypatch.setattr(ax, "fetch_tweet_detail", _fake_detail)
    res = asyncio.run(sf.story_fetch(url="https://x.com/kolo/status/555"))
    assert res.ok
    assert res.data["source"] == "apipass_x"
    assert res.data["text"] == "$MOON is the call"
    assert res.data["fetch_chain"] == ["apipass_x"]


def test_story_fetch_x_profile_url_refused(monkeypatch):
    """A profile/timeline X page (no status id) is refused, never scraped."""
    res = asyncio.run(sf.story_fetch(url="https://x.com/elonmusk"))
    assert not res.ok
    assert "scraping of X is disabled" in res.error


def test_story_fetch_normal_url_still_works(monkeypatch):
    """Non-X URLs keep the normal story pipeline."""
    import types

    fake_story = types.SimpleNamespace(
        ok=True,
        url="https://example.com/article",
        title="T",
        author="A",
        published_at="",
        source="fake",
        chain=["httpx"],
        text="hello world",
    )

    async def _fake_fetch_story(url, timeout):
        return fake_story

    monkeypatch.setattr("rugmunchbot._vendor.rag_pkg.story_ingest.fetch_story", _fake_fetch_story)
    res = asyncio.run(sf.story_fetch(url="https://example.com/article"))
    assert res.ok
    assert res.data["text"] == "hello world"
