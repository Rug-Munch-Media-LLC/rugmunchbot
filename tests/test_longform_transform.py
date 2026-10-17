"""Tests for the longform transform (channels/_longform.py + _longform_lexical).

The three daily reports are authored as Telegram HTML; before they mirror to
Ghost they must become clean longform markdown/lexical. These tests pin the
contract: H1 masthead, H2 sections, real markdown links, no Telegram markup,
no ruled banners, single-blank spacing, decoded entities.
"""

from __future__ import annotations

import json

from rugmunchbot.channels._longform import to_markdown
from rugmunchbot.channels._longform_lexical import to_lexical

SAMPLE = (
    "<b>━━━ MIDDAY MARKET REPORT ━━━</b>\n"
    "<i>Rug Munch Intelligence — the macro tape</i>\n"
    "<i>Friday, October 17, 2026 · Crypto Rug Muncher desk</i>\n"
    "\n"
    "The dollar is doing its best impression of a safe haven again.\n"
    "\n"
    "<b>━━━ THE MACRO TAPE ━━━</b>\n"
    '• Equities slipped as <a href="https://reuters.com/x">Reuters reports</a> the Fed stays put.\n'
    "• Gold held $2,400.\n"
    "\n"
    "## THE FED &amp; THE DATA\n"
    "Jobless claims printed 220k vs 215k expected.\n"
)


def test_masthead_becomes_h1() -> None:
    md = to_markdown(SAMPLE)
    assert md.startswith("# MIDDAY MARKET REPORT")


def test_section_banner_becomes_h2() -> None:
    md = to_markdown(SAMPLE)
    assert "## THE MACRO TAPE" in md


def test_atx_header_preserved_and_entity_decoded() -> None:
    md = to_markdown(SAMPLE)
    assert "## THE FED & THE DATA" in md
    assert "&amp;" not in md


def test_telegram_markup_removed() -> None:
    md = to_markdown(SAMPLE)
    for tag in ("<b>", "</b>", "<i>", "</i>", "<a ", "</a>", "━━━"):
        assert tag not in md


def test_links_become_markdown() -> None:
    md = to_markdown(SAMPLE)
    assert "[Reuters reports](https://reuters.com/x)" in md


def test_bullets_become_markdown_list() -> None:
    md = to_markdown(SAMPLE)
    assert "- Equities slipped" in md
    assert "- Gold held $2,400." in md


def test_no_double_blank_lines() -> None:
    md = to_markdown(SAMPLE)
    assert "\n\n\n" not in md


def test_empty_input_returns_empty() -> None:
    assert to_markdown("") == ""
    assert to_markdown("   ") == ""


def test_idempotent_on_markdown() -> None:
    md = to_markdown(SAMPLE)
    assert to_markdown(md) == md


def test_lexical_has_h1_h2_and_list() -> None:
    doc = json.loads(to_lexical(SAMPLE))
    types = [(c["type"], c.get("tag", "")) for c in doc["root"]["children"]]
    assert ("heading", "h1") in types
    assert ("heading", "h2") in types
    assert ("list", "ul") in types


def test_lexical_link_node_present() -> None:
    doc = json.loads(to_lexical(SAMPLE))
    urls: list[str] = []

    def _walk(nodes: list[dict]) -> None:
        for node in nodes:
            if node.get("type") == "link":
                urls.append(node["url"])
            _walk(node.get("children", []))

    _walk(doc["root"]["children"])
    assert "https://reuters.com/x" in urls


def test_lexical_is_valid_json_string() -> None:
    out = to_lexical(SAMPLE)
    assert isinstance(out, str)
    assert json.loads(out)["root"]["type"] == "root"
