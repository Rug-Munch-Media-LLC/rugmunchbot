"""Test wave 27 — canonical scan report structure (wall-of-text guard).

Contracts read from source (2026-08-22). Pure rendering over a report dict.
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_main import format_scan_report


def _report() -> dict[str, object]:
    return {
        "name": "Pepe",
        "symbol": "PEPE",
        "chain": "ethereum",
        "address": "0x" + "ab" * 20,
        "risk_score": 85,
        "honeypot": False,
        "contract_verified": True,
        "mintable": False,
        "owner_renounced": True,
        "buy_tax": 0.05,
        "sell_tax": 0.05,
        "lp_locked": 0.9,
    }


def test_scan_report_header_and_identity() -> None:
    out = format_scan_report(_report())
    assert out.startswith("\U0001f6e1\ufe0f <b>Rug Munch Token Scan</b>")
    assert "<b>Pepe</b> (PEPE)" in out
    assert "<code>0x" in out  # contract address rendered


def test_scan_report_key_metrics_separated_from_verdict() -> None:
    out = format_scan_report(_report())
    # Wall-of-text guard: a blank line separates the identity/verdict block
    # from the Key Metrics table (heading + thin separator follow the gap).
    assert "\n\n📊 <b>Key Metrics</b>" in out
    assert "Liquidity" in out


def test_scan_report_security_section_renders_checks_and_taxes() -> None:
    out = format_scan_report(_report())
    assert "\U0001f512 <b>Security Assessment</b>" in out
    assert "Honeypot" in out
    assert "Buy Tax: <b>5.0%</b>" in out
    assert "LP Locked: <b>90%</b>" in out


def test_scan_report_market_data_honest_dashes() -> None:
    out = format_scan_report(_report())
    assert "\U0001f4ca <b>Market Data</b>" in out
    # Keep-it-honest rule: unreported values render as em-dashes, never 0.
    assert "Price: <b>\u2014</b>" in out
    assert "Market Cap: <b>\u2014</b>" in out


def test_scan_report_scam_signals_matrix_present() -> None:
    out = format_scan_report(_report())
    assert "\U0001f50d <b>Scam Signals</b>" in out
