"""Tests for the channel roundup poster (scheduler/_posts_roundup.py)."""

from __future__ import annotations

from datetime import UTC, datetime

from rugmunchbot.channels.scheduler._posts_roundup import (
    ROUNDUP_MAX_ITEMS,
    collect_roundup_items,
    compose_roundup,
)


def _post(slug: str, text: str, *, source: str = "", kind: str = "story") -> dict:
    return {"slug": slug, "text": text, "source": source, "kind": kind}


class TestCollectRoundupItems:
    def test_picks_recent_distinct_posts(self) -> None:
        posts = [
            _post("post-1", "Whale dumped 12M PEPE into the void"),
            _post("post-2", "Exploit drained the vault overnight"),
            _post("post-3", "Deployer funded from a mixer again"),
        ]
        items = collect_roundup_items(posts)
        assert [i["slug"] for i in items] == ["post-1", "post-2", "post-3"]

    def test_caps_at_max_items(self) -> None:
        posts = [_post(f"post-{i}", f"Story number {i} about tokens") for i in range(10)]
        assert len(collect_roundup_items(posts)) == ROUNDUP_MAX_ITEMS

    def test_same_source_appears_once(self) -> None:
        posts = [
            _post("post-1", "First take on the exploit", source="https://x.com/a/1"),
            _post("post-2", "Second take on the exploit", source="https://x.com/a/1"),
            _post("post-3", "Unrelated funding news"),
            _post("post-4", "Another unrelated story"),
        ]
        items = collect_roundup_items(posts)
        sources = [i["source"] for i in items if i["source"]]
        assert len(sources) == len(set(sources))
        assert "post-2" not in [i["slug"] for i in items]

    def test_summary_kinds_skipped(self) -> None:
        posts = [
            _post("post-1", "A real story"),
            _post("roundup-1", "Previous roundup", kind="roundup"),
            _post("brief-1", "Market brief", kind="market-summary"),
            _post("post-2", "Another real story"),
            _post("post-3", "Third real story"),
        ]
        slugs = [i["slug"] for i in collect_roundup_items(posts)]
        assert "roundup-1" not in slugs
        assert "brief-1" not in slugs

    def test_banned_phrase_bullet_dropped(self) -> None:
        posts = [
            _post("post-1", "DYOR before you ape into this one"),
            _post("post-2", "A clean, specific story about the rug"),
            _post("post-3", "Third story with real numbers"),
        ]
        slugs = [i["slug"] for i in collect_roundup_items(posts)]
        assert "post-1" not in slugs

    def test_empty_store_yields_nothing(self) -> None:
        assert collect_roundup_items([]) == []


class TestComposeRoundup:
    def test_full_post_shape(self) -> None:
        items = [
            {"slug": "post-1", "label": "Whale churned 12M tokens", "source": ""},
            {"slug": "post-2", "label": "Vault drained overnight", "source": ""},
            {"slug": "post-3", "label": "Mixer-funded deployer", "source": ""},
        ]
        text = compose_roundup(items, now=datetime(2026, 8, 31, tzinfo=UTC))
        assert "THE RUG MUNCH ROUNDUP" in text
        assert text.count("• ") == 3
        assert 'href="https://feed.rugmunch.io/p/post-1"' in text
        # No URL appears twice in the post.
        import re

        urls = re.findall(r'href="([^"]+)"', text)
        assert len(urls) == len(set(urls))

    def test_thin_store_renders_empty(self) -> None:
        items = [{"slug": "post-1", "label": "Only one", "source": ""}]
        assert compose_roundup(items) == ""

    def test_no_banned_phrases_in_output(self) -> None:
        items = [
            {"slug": "post-1", "label": "Whales accumulated 40M tokens", "source": ""},
            {"slug": "post-2", "label": "Exploit drained the pool", "source": ""},
            {"slug": "post-3", "label": "Deployer funded from a mixer", "source": ""},
        ]
        text = compose_roundup(items).lower()
        for banned in ("nfa", "dyor", "wagmi", "delve into", "ever-evolving", "stay tuned"):
            assert banned not in text
