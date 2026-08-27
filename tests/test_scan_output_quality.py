"""Tests for scan report formatters — verify output quality and structure."""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_main import (
    _evidence_verdict_line,
    format_deep_scan_report,
    format_scan_report,
)


class TestEvidenceVerdictLine:
    """Tests for the evidence-driven verdict line."""

    def test_honeypot_detected(self):
        r = {"honeypot": True}
        result = _evidence_verdict_line(r)
        assert "Honeypot" in result

    def test_mint_authority_live(self):
        r = {"honeypot": False, "mintable": True}
        result = _evidence_verdict_line(r)
        assert "Mint authority" in result

    def test_freeze_authority_live(self):
        r = {"honeypot": False, "mint_authority": False, "freezable": True}
        result = _evidence_verdict_line(r)
        assert "Freeze authority" in result

    def test_bundled_launch(self):
        r = {
            "honeypot": False,
            "mint_authority": False,
            "freeze_authority": False,
            "bundle_analysis": {"is_bundled": True},
        }
        result = _evidence_verdict_line(r)
        assert "Bundled" in result

    def test_clean_token(self):
        r = {
            "honeypot": False,
            "mint_authority": False,
            "freeze_authority": False,
            "bundle_analysis": {"is_bundled": False},
            "contract_verified": True,
            "risk_signals": {},
        }
        result = _evidence_verdict_line(r)
        assert "passed" in result.lower() or result == ""


class TestScanReportFormatting:
    """Tests for scan report formatting."""

    def test_basic_scan_report(self):
        r = {
            "name": "Test Token",
            "symbol": "TEST",
            "address": "0xabc123",
            "chain": "ethereum",
            "risk_score": 45,
            "risk_level": "MEDIUM",
            "honeypot": False,
            "mint_authority": False,
            "freeze_authority": False,
            "contract_verified": True,
            "liquidity": 50000,
            "holders": 250,
            "total_supply": 1000000,
            "bundle_analysis": {"is_bundled": False},
            "risk_signals": {},
        }
        report = format_scan_report(r)
        assert "Rug Munch" in report
        assert "Test Token" in report
        assert "TEST" in report
        assert "0xabc123" in report

    def test_deep_scan_report(self):
        r = {
            "name": "Deep Token",
            "symbol": "DEEP",
            "address": "0xdef456",
            "chain": "base",
            "risk_score": 20,
            "risk_level": "LOW",
            "honeypot": False,
            "mint_authority": False,
            "freeze_authority": False,
            "contract_verified": True,
            "liquidity": 100000,
            "holders": 500,
            "total_supply": 10000000,
            "bundle_analysis": {"is_bundled": False},
            "risk_signals": {},
        }
        report = format_deep_scan_report(r)
        assert "Rug Munch" in report
        assert "Deep Token" in report
        assert "DEEP" in report

    def test_high_risk_report(self):
        r = {
            "name": "Scam Token",
            "symbol": "SCAM",
            "address": "0xbad789",
            "chain": "ethereum",
            "risk_score": 95,
            "risk_level": "CRITICAL",
            "honeypot": True,
            "liquidity": 100,
            "holders": 5,
            "total_supply": 100,
            "bundle_analysis": {"is_bundled": True},
            "risk_signals": {"wash_trading": True},
        }
        report = format_scan_report(r)
        assert "Honeypot" in report or "CRITICAL" in report


class TestChatAIQuality:
    """Tests for chat AI response quality."""

    def test_fallback_message_exists(self):
        from rugmunchbot.handlers.chat_ai._routing._core import _FALLBACK_MESSAGE

        assert isinstance(_FALLBACK_MESSAGE, str)
        assert len(_FALLBACK_MESSAGE) > 20

    def test_routing_constants(self):
        from rugmunchbot.handlers.chat_ai._routing._constants import (
            MAX_MESSAGE_CHARS,
            SCAN_ENDPOINT,
        )

        assert isinstance(SCAN_ENDPOINT, str)
        assert "scan" in SCAN_ENDPOINT.lower()
        assert MAX_MESSAGE_CHARS > 0
