"""Every outbound content lane must drive traffic to the full story.

Lane coverage: 3 daily pieces (morning/midday/evening), news, scam call-outs,
shill campaigns, smart-money, whales, predictions. Builders that render text
are asserted directly; async daily pipelines are asserted at the wiring level
(the module calls ``attach_story_cta`` with its lane) so a refactor that drops
the CTA fails here instead of silently shipping a linkless post.
"""

from __future__ import annotations

from pathlib import Path

from rugmunchbot.channels._story_cta import CTA_MARKER
from rugmunchbot.channels.formatters._news import (
    format_breaking_post,
    format_feature_post,
    format_hybrid_post,
    format_news_post,
    format_themed_post,
)
from rugmunchbot.channels.formatters._scam_alert import format_scam_alert
from rugmunchbot.smart_money.render_base import compose

ROOT = Path(__file__).resolve().parents[1] / "src" / "rugmunchbot" / "channels"

ITEM = {
    "title": "BTC slips",
    "url": "https://x.com/a",
    "source": "TheBlock",
    "summary": "Bitcoin fell today while whales accumulated.",
    "commentary": "Whales bought the dip.",
    "image_url": "https://img.example/a.png",
}

SCAM = {"severity": "high", "address": "0xabc", "scam_intel": "drainer"}


def _assert_cta(post: str, section: str) -> None:
    assert CTA_MARKER in post or "More on this story" in post
    assert "rugmunch.io" in post
    assert section in post


def test_news_digest_carries_cta() -> None:
    _assert_cta(format_news_post([ITEM]), "/news")


def test_news_breaking_carries_cta() -> None:
    _assert_cta(format_breaking_post(ITEM), "/news")


def test_news_feature_carries_cta() -> None:
    _assert_cta(format_feature_post(ITEM), "/news")


def test_news_hybrid_carries_cta() -> None:
    _assert_cta(format_hybrid_post([ITEM]), "/news")


def test_news_themed_carries_cta() -> None:
    _assert_cta(format_themed_post([ITEM], theme="DeFi"), "/news")


def test_scam_callout_carries_cta() -> None:
    _assert_cta(format_scam_alert(SCAM), "/scam-school")


def test_smart_money_compose_carries_cta() -> None:
    _assert_cta(
        compose(
            headline="h",
            evidence=["e"],
            source="s",
            updated=None,
            risk="r",
            lane="smart_money",
        ),
        "/market-intel",
    )


def test_whales_compose_carries_cta() -> None:
    _assert_cta(
        compose(headline="h", evidence=["e"], source="s", updated=None, risk="r", lane="whales"),
        "/market-intel",
    )


def test_predictions_compose_uses_real_slug() -> None:
    post = compose(
        headline="h",
        evidence=["e"],
        source="s",
        updated=None,
        risk="r",
        lane="predictions",
        payload={"slug": "fed-cut-march"},
    )
    assert "https://rugmunch.io/story/fed-cut-march" in post
    assert "utm_campaign=predictions" in post


def test_compose_without_lane_is_unchanged() -> None:
    post = compose(headline="h", evidence=["e"], source="s", updated=None, risk="r")
    assert "rugmunch.io" not in post


def test_daily_pipelines_call_attach_with_their_lane() -> None:
    for rel, lane in (
        ("daily_rug/_publish.py", "morning"),
        ("daily_market_summary/_publish/send.py", "midday"),
        ("evening_wrap/_publish.py", "evening"),
        ("scheduler/_posts_whales.py", "whales"),
        ("scammer_warning.py", "scam"),
        ("shill_campaign_post.py", "shill"),
    ):
        source = (ROOT / rel).read_text()
        assert "attach_story_cta" in source, rel
        assert f'"{lane}"' in source, f"{rel} lost lane {lane}"
