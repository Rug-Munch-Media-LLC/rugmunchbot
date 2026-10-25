"""Formatter limit contract — a card is never longer than Telegram accepts.

``fit_report`` used to return the ORIGINAL over-limit text when trimming the
optional parity block was not enough. Telegram then rejected the whole message
("message is too long"), so a verbose provider payload meant no card at all.
The contract now: the result always fits, always keeps its HTML balanced, and
says so when it had to cut.
"""

from __future__ import annotations

from rugmunchbot.content_style._normalize import balance_tags
from rugmunchbot.formatters._scan_report_main import format_scan_report
from rugmunchbot.formatters._scan_report_parity import (
    HEADING,
    TELEGRAM_LIMIT,
    TRUNCATION_MARKER,
    fit_report,
)

CAPTION_LIMIT = 1024


def test_fit_report_never_exceeds_the_telegram_limit() -> None:
    long_report = "<b>Head</b>\n" + ("verbose provider line\n" * 400) + "<code>x</code>"
    out = fit_report(long_report)
    assert len(out) <= TELEGRAM_LIMIT
    assert balance_tags(out) == out
    assert out.endswith(TRUNCATION_MARKER)


def test_fit_report_respects_a_caption_limit() -> None:
    out = fit_report("<b>Head</b>\n" + "line\n" * 900, limit=CAPTION_LIMIT)
    assert len(out) <= CAPTION_LIMIT
    assert balance_tags(out) == out


def test_fit_report_drops_the_parity_block_before_truncating() -> None:
    filler = "\n".join(f"row {i}" for i in range(40))
    text = "\n".join(["<b>Core</b>", filler, "", HEADING, "parity body"])
    for limit in (len(text) - 1, len(text) - 5, len(text) - 20):
        out = fit_report(text, limit=limit)
        assert len(out) <= limit
        assert HEADING not in out
        assert "<b>Core</b>" in out


def test_fit_report_leaves_a_fitting_report_byte_identical() -> None:
    text = "<b>Rug Munch Token Scan</b>\n<code>0xabc</code>"
    assert fit_report(text) == text


def test_scan_report_with_hostile_long_payload_fits() -> None:
    report = {
        "name": "A<B & " + "long " * 300,
        "symbol": "X&Y",
        "chain": "ethereum",
        "address": "0x" + "ab" * 20,
        "risk_score": 99,
        "description": "& <b> " * 2000,
        "ai_verdict": "very long <verdict> & text " * 200,
    }
    # Boundary moved 2026-10-26: the formatter emits the whole card (a tail cut
    # was eating the risk-report/ref-link sections); the SEND path owns the
    # Telegram limit via ``fit_report``. The guarantee is unchanged: what goes
    # out never exceeds the limit and is never unbalanced HTML.
    out = fit_report(format_scan_report(report))
    assert len(out) <= TELEGRAM_LIMIT
    assert balance_tags(out) == out
    assert "<b>A<B" not in out
