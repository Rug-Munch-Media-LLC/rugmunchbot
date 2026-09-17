"""Unit tests for the pure helpers in channels/_alert_story."""

from rugmunchbot.channels import _alert_story as s


def test_evidence_joins_core_fields():
    item = {
        "risk_level": "high",
        "summary": "Rugged 40 mins after launch",
        "verdict": "rug",
        "address": "0xAbC",
    }
    out = s._evidence(item)
    assert "severity=high" in out
    assert "summary=Rugged 40 mins after launch" in out
    assert "verdict=rug" in out
    assert "address=0xAbC" in out


def test_evidence_handles_missing_fields():
    out = s._evidence({})
    assert "severity=?" in out


def test_scanner_url_passthrough():
    assert s._scanner_url({"scanner_url": "https://x/y"}) == "https://x/y"
    assert s._scanner_url({}) == ""


def test_system_prompt_rules_present():
    p = s._SYSTEM_PROMPT
    assert "NEVER invent facts" in p
    assert "No 'BREAKING' clickbait" in p
