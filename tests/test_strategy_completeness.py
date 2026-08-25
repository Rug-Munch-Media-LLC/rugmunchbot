"""Strategy v2 completeness bar — honest 'unavailable' on deep scan cards.

CONTENT-STRATEGY-CANONICAL v2 / SCAN-QUALITY-PLAN: every core section
renders or says "unavailable" honestly — a silent section reads as
"checked, fine", which is a lie when the data source failed.

Covers:
* Deep card Holder Distribution — absent analytics → explicit unavailable
  block (never silently omitted).
* Deep key metrics Bundle row — absent bundle payload → ⚪ N/A row
  (silence would read as "scanned, clean").
* Deep key metrics Bundle row — present payload still renders its verdict.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_main import format_deep_scan_report


def _report(**extra: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "name": "Pepe",
        "symbol": "PEPE",
        "chain": "ethereum",
        "address": "0x" + "ab" * 20,
        "risk_score": 85,
        "honeypot": False,
        "contract_verified": True,
        "mintable": False,
        "owner_renounced": True,
    }
    r.update(extra)
    return r


# ── Holder Distribution honesty ──────────────────────────────────────


def test_deep_card_holder_distribution_absent_renders_unavailable() -> None:
    """No holder analytics at all → section renders with honest marker."""
    out = format_deep_scan_report(_report())
    assert "\U0001f465 <b>Holder Distribution</b>" in out
    assert "unavailable" in out


def test_deep_card_holder_distribution_present_renders_data() -> None:
    out = format_deep_scan_report(_report(holder_analysis={"top_10_pct": 61.2, "top_20_pct": 74.0}))
    assert "\U0001f465 <b>Holder Distribution</b>" in out
    assert "<b>61.2%</b>" in out
    assert "unavailable" not in out.split("Holder Distribution")[1].split("\n\n")[0]


def test_deep_card_holder_distribution_gini_only_still_renders_rows() -> None:
    """A gini-only payload yields rows (verdict path), not the N/A block."""
    out = format_deep_scan_report(_report(holder_analysis={"gini_coefficient": 0.91}))
    assert "\U0001f465 <b>Holder Distribution</b>" in out
    assert "unavailable \u2014 no holder analytics" not in out


# ── Bundle verdict honesty (deep key metrics) ────────────────────────


def _bundle_row(out: str) -> str:
    """The key-metrics 'Bundle' table row (line-scoped — other rows may
    legitimately render their own N/A markers)."""
    for line in out.split("\n"):
        if line.strip().startswith("Bundle"):
            return line
    return ""


def test_deep_card_bundle_payload_absent_renders_na_row() -> None:
    """No bundle_analysis dict at all → explicit ⚪ N/A, never silence."""
    out = format_deep_scan_report(_report())
    assert "⚪ <b>N/A</b>" in _bundle_row(out)


def test_deep_card_bundle_payload_present_renders_verdict_not_na() -> None:
    out = format_deep_scan_report(
        _report(bundle_analysis={"is_bundled": True, "wallets": ["0x1"], "confidence": 0.9})
    )
    row = _bundle_row(out)
    assert "Bundle detected" in row
    assert "N/A" not in row


def test_deep_card_bundle_empty_scan_renders_honest_dash_not_na() -> None:
    """Engine ran but found nothing conclusive → '—' (existing honesty), not N/A."""
    out = format_deep_scan_report(_report(bundle_analysis={"is_bundled": False, "wallets": []}))
    assert "N/A" not in _bundle_row(out)


# ── basic /scan card untouched by the deep-only additions ────────────


def test_basic_card_has_no_holder_distribution_section() -> None:
    from rugmunchbot.formatters._scan_report_main import format_scan_report

    out = format_scan_report(_report())
    assert "Holder Distribution" not in out
