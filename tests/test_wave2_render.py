"""Test wave 2 — render paths, report text helpers, keyboard builders.

Behavior documented from actual outputs (2026-08-21). Pure construction
only: no network, no DB, no telegram network calls.
"""

from __future__ import annotations

import rugmunchbot.keyboards as kb
from rugmunchbot.formatters._scan_report_common import _clip, _collapse_blank_lines
from rugmunchbot.tools.intent import _fmt_signal

# ── tools/intent render ──────────────────────────────────────────────────────


def test_fmt_signal_usd_scales() -> None:
    assert _fmt_signal("price_usd", 1234.5) == "$1.23K"


def test_fmt_signal_pct_signed() -> None:
    assert _fmt_signal("change_24h", -12.34) == "-12.3%"


def test_fmt_signal_unknown_field_passthrough() -> None:
    assert _fmt_signal("unknown_field", "x") == "x"


# ── scan-report text helpers ─────────────────────────────────────────────────


def test_clip_truncates_with_ellipsis() -> None:
    assert _clip("hello world", 8) == "hello w…"


def test_clip_short_text_untouched() -> None:
    assert _clip("hi", 8) == "hi"


def test_collapse_blank_lines() -> None:
    assert _collapse_blank_lines("a\n\n\n\nb") == "a\n\nb"


# ── keyboard builders ────────────────────────────────────────────────────────


def test_menu_kb_shape() -> None:
    markup = kb.menu_kb()
    assert len(markup.inline_keyboard) == 4
    assert markup.inline_keyboard[0][0].text.startswith("\U0001f50d Scan")


def test_pricing_kb_has_star_prices() -> None:
    markup = kb.pricing_kb()
    texts = [b.text for row in markup.inline_keyboard for b in row]
    assert any("\u2605" in t for t in texts)


def test_back_kb_routes_to_main_menu() -> None:
    markup = kb.back_kb()
    assert markup.inline_keyboard[0][0].callback_data == "menu_main"


def test_scan_result_kb_carries_address() -> None:
    addr = "0x" + "a" * 40
    markup = kb.scan_result_kb(addr, "ethereum")
    callbacks = [b.callback_data or "" for row in markup.inline_keyboard for b in row]
    assert any(addr in c for c in callbacks)
