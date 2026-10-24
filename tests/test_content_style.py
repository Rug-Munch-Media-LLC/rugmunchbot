"""Golden + invariant tests for the canonical Telegram style renderer.

Covers every surface family (scans, news, feed, alerts, Daily Rug, premium,
reports) by feeding a representative post through ``prepare_for_send`` and
asserting the layout contract: no double blank lines, balanced HTML, no
truncation, no duplicate links, every part within the Telegram ceiling.
"""

from __future__ import annotations

import re

import pytest

from rugmunchbot.content_style import (
    TELEGRAM_LIMIT,
    balance_tags,
    dedupe_links,
    normalize,
    prepare_for_send,
    render_daily_rug,
    render_post,
    split_html,
)

# ── Representative post per surface (real shapes, trimmed data) ───────────
SCAN = (
    "<b>SCAN · $WIF (Solana)</b>\n\n\n"
    "<b>Verdict:</b> CAUTION\n\n"
    "- LP unlocked 41%\n* Deployer linked to 3 rugs\n"
    "\u2022 Mint authority active\n\n"
    '<a href="https://rugmunch.io/scan/abc">rugmunch.io/scan/abc</a>\n'
    '<a href="https://rugmunch.io/scan/abc">rugmunch.io/scan/abc</a>'
)
NEWS = (
    "<b>SEC charges three in $65M Ponzi</b>\n\n\n\n"
    "Returns were funded by new deposits.\n\n"
    '<a href="https://example.com/a">source</a>\n\n'
    '<a href="https://example.com/a">source</a>'
)
FEED = "<b>\u2501\u2501\u2501 THE DAILY RUG \u2501\u2501\u2501</b>\nLede paragraph.\n\n\nBody paragraph."
ALERT = (
    "<b>\U0001f6a8 RUGPULL \u2014 $DRIP</b>\n\nLP drained 47 minutes after launch.\n\nRisk: 97/100"
)
PREMIUM = "<b>PREMIUM WATCH</b>\n\n\n- watchlist: $SOL\n- threshold: 5%"
REPORT = "<b>\u2501\u2501\u2501 MIDDAY \u2501\u2501\u2501</b>\n\n" + (
    "Sentence about flows. " * 400
)

SURFACES = {
    "scan": SCAN,
    "news": NEWS,
    "feed": FEED,
    "alert": ALERT,
    "premium": PREMIUM,
    "report": REPORT,
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_every_surface_is_canonical(surface: str) -> None:
    parts = prepare_for_send(SURFACES[surface])
    assert parts, surface
    joined = "\n".join(parts)
    assert "\n\n\n" not in joined, "double blank line leaked"
    assert not re.search(r"[ \t]+\n", joined), "trailing whitespace"
    for part in parts:
        assert len(part) <= TELEGRAM_LIMIT
        assert part.count("<b>") == part.count("</b>"), (surface, "b")
        assert part.count("<i>") == part.count("</i>"), (surface, "i")
        assert len(re.findall(r"<a\s", part)) == part.count("</a>"), (surface, "a")
    hrefs = re.findall(r'href="([^"]+)"', joined)
    assert len(hrefs) == len(set(hrefs)), "duplicate link shipped"


def test_no_mid_tag_truncation() -> None:
    text = "<b>" + ("word " * 3000) + "</b>"
    parts = split_html(text, 1000)
    assert len(parts) > 1
    for part in parts:
        assert part.count("<b>") == part.count("</b>")
        assert not part.endswith("<")
    assert all(len(p) <= 1000 for p in parts)


def test_entity_never_split() -> None:
    text = ("&amp; " * 400).strip()
    for part in split_html(text, 500):
        assert not part.endswith("&")
        assert not re.search(r"&[a-zA-Z]{1,3}$", part), "partial entity at part end"


def test_bullets_and_blank_lines() -> None:
    out = normalize("- one\n* two\n\u2014 three")
    assert out == "\u2022 one\n\u2022 two\n\u2022 three"


def test_duplicate_link_becomes_label() -> None:
    out = dedupe_links('<a href="https://x.io/a">A</a> and <a href="https://x.io/a">B</a>')
    assert out.count("href=") == 1
    assert "B" in out


def test_unclosed_tags_are_closed() -> None:
    assert balance_tags("<b>bold text") == "<b>bold text</b>"


def test_prepare_empty() -> None:
    assert prepare_for_send("") == []
    assert prepare_for_send("   ") == []


# ── Golden snapshots ─────────────────────────────────────────────────────
def test_render_post_golden() -> None:
    out = render_post(
        title="SCAN \u2014 $WIF",
        summary=["Deployer linked to 3 prior rugs.", "LP unlocked 41%."],
        key_line="Risk 78/100",
        payload={"story_slug": "wif-scan"},
        lane="morning",
    )
    assert out == (
        "<b>\u2501\u2501\u2501 SCAN \u2014 $WIF \u2501\u2501\u2501</b>\n\n"
        "Deployer linked to 3 prior rugs.\n\n"
        "LP unlocked 41%.\n\n"
        "\U0001f4ca Risk 78/100\n\n"
        '\U0001f4d6 <a href="https://rugmunch.io/story/wif-scan?utm_source=telegram'
        "&amp;utm_medium=bot&amp;utm_campaign=morning&amp;utm_content=rugmunchbot"
        '&amp;ref=rugmunchbot">Read the full story \u2192</a>\n\n'
        '\U0001f916 <a href="https://t.me/RugMunchBot">@RugMunchBot</a> \u2014 scan any token, anytime'
    )


def test_daily_rug_golden_shape() -> None:
    out = render_daily_rug(
        date_label="Saturday, October 24, 2026",
        summary=["Line one.", "Line two.", "Line three."],
        key_line="$SOL +4.2% on $18.4B volume",
        slug="daily-rug-2026-10-24",
    )
    lines = [line for line in out.split("\n") if line.strip()]
    assert lines[0].startswith("<b>\u2501\u2501\u2501 The Daily Rug")
    assert lines[1] == "<i>Saturday, October 24, 2026</i>"
    assert sum(1 for line in lines if line.startswith("Line ")) == 3
    assert any(line.startswith("\U0001f4ca ") for line in lines)
    assert "https://rugmunch.io/story/daily-rug-2026-10-24" in out
    assert "Read the full story" in out
    assert "@RugMunchBot" in out
    assert out.count("Read the full story") == 1


def test_daily_rug_without_slug_degrades_to_section_page() -> None:
    out = render_daily_rug(date_label="d", summary=["x"], slug="")
    assert "/story/" not in out
    assert "rugmunch.io/feed" in out or "More on this story" in out
