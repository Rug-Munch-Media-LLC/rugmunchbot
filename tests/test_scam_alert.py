"""Tests for the dedicated scam-alert post type (formatters._scam_alert).

Oracle note (AI-TEST-ORACLE-POLICY): each test drives the real formatter
against hand-built fixtures and asserts exact outcomes — severity framing
maps to the right banner, the concrete intel renders, the verify link is
present, the dedicated graphic is attached, and a signal-less item is not
postable. No tautological smoke checks.
"""

from __future__ import annotations

from rugmunchbot.channels.formatters import (
    SEVERITY_FRAMING,
    format_scam_alert,
    is_postable_scam_alert,
)
from rugmunchbot.support.post_graphics import graphic_for

_CRITICAL = {
    "severity": "critical",
    "name": "RugCoin",
    "symbol": "RUG",
    "address": "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    "chain": "ethereum",
    "scam_intel": "Honeypot: sells blocked after buy. LP pulled 4 min after launch.",
    "source": "https://x.com/example/status/1",
}


def test_critical_severity_framing() -> None:
    """A critical alert carries the critical banner + do-not-interact framing."""
    post = format_scam_alert(_CRITICAL)
    assert "CRITICAL SCAM ALERT" in post
    assert "Do not interact" in post


def test_severity_framing_table_covers_all_levels() -> None:
    """Every severity level has a distinct banner label."""
    labels = {SEVERITY_FRAMING[s][1] for s in ("critical", "high", "medium", "low")}
    assert len(labels) == 4


def test_unknown_severity_uses_default_framing() -> None:
    """An unknown severity never invents a verdict — it uses the default."""
    post = format_scam_alert({"severity": "banana", "address": "0xabc"})
    assert "SCAM ALERT" in post
    assert "CRITICAL" not in post


def test_concrete_intel_renders() -> None:
    """The actual mechanism renders under the What's wrong section."""
    post = format_scam_alert(_CRITICAL)
    assert "What's wrong" in post
    assert "Honeypot" in post


def test_verify_link_and_scan_command_present() -> None:
    """The verify link + /scan command are front and centre."""
    post = format_scam_alert(_CRITICAL)
    assert "rugmunch.io/scanner?token=" in post
    assert "/scan" in post
    assert _CRITICAL["address"] in post


def test_dedicated_graphic_attached() -> None:
    """The card carries the scam-alert brand graphic."""
    post = format_scam_alert(_CRITICAL)
    assert post.image_url == graphic_for("scam_alert")
    assert post.image_url != ""


def test_risk_flags_render_when_no_intel() -> None:
    """With no intel string, the risk flags render as the mechanism."""
    post = format_scam_alert(
        {"severity": "high", "address": "0xabc", "risk_flags": ["honeypot", "lp_removed"]}
    )
    assert "honeypot" in post
    assert "lp_removed" in post


def test_is_postable_requires_signal() -> None:
    """A card with no address, intel or flags is not a scam alert."""
    assert is_postable_scam_alert(_CRITICAL) is True
    assert is_postable_scam_alert({"address": "0xabc"}) is True
    assert is_postable_scam_alert({"scam_intel": "drainer"}) is True
    assert is_postable_scam_alert({"risk_flags": ["honeypot"]}) is True
    assert is_postable_scam_alert({}) is False
    assert is_postable_scam_alert({"name": "Nothing"}) is False


def test_no_address_omits_scan_command() -> None:
    """A card without an address still renders but omits the /scan command."""
    post = format_scam_alert({"severity": "medium", "scam_intel": "thin LP, flagged deployer"})
    assert "/scan <code>" not in post
    assert "thin LP" in post
