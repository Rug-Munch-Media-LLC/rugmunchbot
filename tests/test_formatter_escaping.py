"""Formatter escaping contract — user-controlled text must never break HTML.

A card built from a hostile token name (``A<B``, ``A & B``, ``</code>``) used
to reach Telegram with raw ``<``/``&``, so the API rejected the WHOLE message
("can't parse entities") and the user got nothing instead of a scan. Every
value that comes from a provider, a ticker or a user is escaped, and ``None``
renders empty — never the literal ``"None"``.
"""

from __future__ import annotations

from rugmunchbot.channels.formatters._news import format_news_post
from rugmunchbot.channels.formatters._scam_alert import format_scam_alert
from rugmunchbot.channels.scheduler._posts_whales import _fmt_doc
from rugmunchbot.content_style._normalize import balance_tags
from rugmunchbot.formatters import clip, esc
from rugmunchbot.formatters._scan_report_main import format_scan_report

HOSTILE = "A<B & </code>"
ADDRESS = "0x" + "ab" * 20


def _scan(**over: object) -> dict[str, object]:
    report: dict[str, object] = {
        "name": "Pepe",
        "symbol": "PEPE",
        "chain": "ethereum",
        "address": ADDRESS,
        "risk_score": 85,
        "honeypot": False,
        "contract_verified": True,
        "lp_locked": 0.9,
    }
    report.update(over)
    return report


def test_canonical_helpers_escape_and_clip() -> None:
    assert esc("A<B & C") == "A&lt;B &amp; C"
    assert esc(None) == ""
    assert esc("Ünïcode 🚀") == "Ünïcode 🚀"  # emoji/unicode untouched
    assert clip("abcd", 10) == "abcd"  # no marker when nothing was cut
    assert clip("x" * 20, 10) == "x" * 9 + "\u2026"
    assert clip(None, 10) == ""


def test_scan_card_escapes_hostile_identity() -> None:
    out = format_scan_report(_scan(name=HOSTILE, symbol="<b>x</b>"))
    assert "A&lt;B &amp; &lt;/code&gt;" in out
    assert "<b>A<B" not in out
    assert balance_tags(out) == out  # every tag closed, none stray
    assert len(out) <= 4096


def test_scan_card_missing_identity_renders_empty_not_none() -> None:
    out = format_scan_report(_scan(name=None, symbol=None, address=None))
    assert "None" not in out
    assert "nan" not in out.lower()


def test_scan_card_keeps_unicode_and_emoji() -> None:
    out = format_scan_report(_scan(name="Łüna 🚀", symbol="ŁNA"))
    assert "<b>Łüna 🚀</b>" in out
    assert balance_tags(out) == out


def test_news_card_escapes_headline_and_keeps_emoji() -> None:
    post = format_news_post(
        [
            {
                "title": "BTC <slips> & dips 🚀",
                "url": "https://x.com/a?b=1&c=2",
                "source": "The<Block>",
                "summary": "Fell <b>hard</b> today.",
            }
        ]
    )
    assert "BTC &lt;slips&gt; &amp; dips 🚀" in post
    assert 'href="https://x.com/a?b=1&amp;c=2"' in post
    assert "<b>hard</b>" not in post  # scraped markup never becomes our tags
    assert balance_tags(post) == post


def test_scam_card_escapes_address_and_name() -> None:
    post = format_scam_alert(
        {
            "severity": "critical",
            "name": HOSTILE,
            "symbol": "SCAM",
            "address": ADDRESS,
            "chain": "ethereum",
            "intel": ["LP pulled <now>"],
        }
    )
    assert "A&lt;B &amp; &lt;/code&gt;" in post
    assert f"<code>{ADDRESS}</code>" in post
    assert balance_tags(post) == post


def test_whale_card_escapes_wallet_metadata() -> None:
    line = _fmt_doc(
        "whale:1",
        {
            "metadata": {
                "type": "large_transfer",
                "chain": "ethereum",
                "from": "0x" + "11" * 20,
                "to": "0x" + "22" * 20,
                "label": "Binance <hot> & cold",
                "amount_usd": 12_500_000,
            }
        },
    )
    assert line is not None
    assert "Binance &lt;hot&gt; &amp; cold" in line
    assert balance_tags(line) == line
