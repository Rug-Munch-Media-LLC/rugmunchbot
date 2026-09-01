"""Wave 9 — channels.formatters._news: digest / breaking / feature / hybrid /
themed post builders.

All expectations probed against the implementation before asserting (oracle
policy). NewsPost is a str subclass carrying ``.image_url``; only http(s)
images are picked up (``javascript:`` rejected).
"""

from __future__ import annotations

from rugmunchbot.channels.formatters._news import (
    format_breaking_post,
    format_feature_post,
    format_hybrid_post,
    format_news_post,
    format_themed_post,
)

ITEM_A = {
    "title": "BTC <slips>",
    "url": "https://x.com/a",
    "source": "TheBlock",
    "summary": "Bitcoin fell today. Whales accumulated. More detail here.",
    "commentary": "Whales buying dips.",
    "image_url": "https://img.example/a.png",
}
ITEM_B = {
    "title": "ETH up",
    "url": "",
    "source": "CoinDesk",
    "summary": "",
    "image_url": "javascript:alert(1)",
}


def test_news_post_digest_shape_and_image() -> None:
    post = format_news_post([ITEM_A, ITEM_B])
    assert type(post).__name__ == "NewsPost"
    assert "📰 <b>RugMunch News</b>" in post
    assert '1. <a href="https://x.com/a">BTC &lt;slips&gt;</a>' in post
    assert "Bitcoin fell today. Whales accumulated. More detail here. — <i>TheBlock</i>" in post
    assert "💡 Whales buying dips." in post
    assert "2. ETH up" in post  # no url → unlinked headline
    assert "— <i>CoinDesk</i>" in post
    assert "@RugMunchBot" in post  # CTA
    assert "a.png" in post.image_url
    assert "/api/v1/media/thumb?url=" in post.image_url


def test_news_post_rejects_non_http_image() -> None:
    post = format_news_post([ITEM_B])
    assert post.image_url == ""


def test_news_post_empty_items_still_renders_shell() -> None:
    post = format_news_post([])
    assert "📰 <b>RugMunch News</b>" in post
    assert post.image_url == ""


def test_breaking_post_links_and_carries_image() -> None:
    post = format_breaking_post(ITEM_A)
    assert "🚨 <b>BREAKING</b>" in post
    assert '<a href="https://x.com/a">BTC &lt;slips&gt;</a>' in post
    assert "💡 <b>Why it matters:</b> Whales buying dips." in post
    assert 'Full story: <a href="https://x.com/a">TheBlock</a>' in post
    assert "a.png" in post.image_url
    assert "/api/v1/media/thumb?url=" in post.image_url


def test_breaking_post_without_url_plain_source() -> None:
    item = dict(ITEM_B, summary="Eth rallied hard.")
    post = format_breaking_post(item)
    assert "🔗 <i>Full story: CoinDesk</i>" in post
    # The story headline itself stays unlinked (footer links still appear).
    story_line = next(ln for ln in post.splitlines() if "Eth rallied hard." in ln or "ETH up" in ln)
    assert "<a href=" not in story_line


def test_feature_post_hook_lede_split() -> None:
    post = format_feature_post(ITEM_A)
    assert "⭐ <b>Top Story</b>" in post
    lines = post.splitlines()
    hook_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "Bitcoin fell today.")
    lede_idx = next(
        i for i, ln in enumerate(lines) if ln.strip() == "Whales accumulated. More detail here."
    )
    assert hook_idx < lede_idx


def test_hybrid_lead_plus_numbered_rest() -> None:
    post = format_hybrid_post([ITEM_A, ITEM_B])
    assert '⭐ <a href="https://x.com/a">BTC &lt;slips&gt;</a>' in post
    assert "More:" in post
    assert "2. ETH up — <i>CoinDesk</i>" in post
    assert "a.png" in post.image_url
    assert "/api/v1/media/thumb?url=" in post.image_url


def test_hybrid_empty_items_renders_shell() -> None:
    post = format_hybrid_post([])
    assert "📰 <b>RugMunch News</b>" in post
    assert post.image_url == ""


def test_themed_post_label_and_default() -> None:
    themed = format_themed_post([ITEM_A], theme="$BTC")
    assert "🧵 <b>RugMunch News · $BTC Roundup</b>" in themed
    default = format_themed_post([ITEM_A])
    assert "🧵 <b>RugMunch News · Top Stories Roundup</b>" in default


def test_themed_post_escapes_theme() -> None:
    themed = format_themed_post([ITEM_A], theme="<script>")
    assert "<script>" not in themed
    assert "&lt;script&gt;" in themed
