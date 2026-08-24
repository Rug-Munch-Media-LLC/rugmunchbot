"""Wave 42 — _ai_verdict fact projection contracts (was 19% covered).

The fact set is what the LLM verdict may cite — fabrication prevention
starts here. Contracts:

* legacy top-level fields project through
* v2 evidence security checks (HP01/CR03) fill gaps when legacy absent
* risk_score coercion: numeric ok, junk → None
* lp_locked percent→fraction conversion from evidence
* hostile payloads (non-dict evidence/checks) degrade safely
* threats capped at 6
"""

from __future__ import annotations

from rugmunchbot.tools.scanner._ai_verdict import _verdict_facts


def test_legacy_fields_project():
    result = {
        "name": "Test",
        "symbol": "TST",
        "chain": "ethereum",
        "risk_score": 42.7,
        "honeypot": False,
        "mintable": True,
        "lp_locked": 0.85,
        "holders": 812,
        "threats": ["A", "B"],
    }
    f = _verdict_facts(result)
    assert f["name"] == "Test"
    assert f["risk_score"] == 42.7
    assert f["honeypot"] is False
    assert f["mintable"] is True
    assert f["lp_locked"] == 0.85
    assert f["total_holders"] == 812
    assert f["threats"] == ["A", "B"]


def test_v2_evidence_checks_fill_gaps():
    result = {
        "evidence": {
            "security": {
                "checks": {
                    "HP01": {"status": "fail", "details": "x"},
                    "CR03": {"status": "warn", "details": "y"},
                }
            },
            "holders": {"holder_count": 300, "top_10_pct": 55.0},
            "liquidity": {"locked_pct": 90},
        }
    }
    f = _verdict_facts(result)
    assert f["honeypot"] == "fail"
    assert f["mintable"] == "warn"
    assert f["total_holders"] == 300
    assert f["top_10_holder_pct"] == 55.0
    assert f["lp_locked"] == pytest_approx(0.9)


def pytest_approx(v):
    import pytest

    return pytest.approx(v)


def test_risk_score_junk_coerces_to_none():
    f = _verdict_facts({"risk_score": "not-a-number"})
    assert f["risk_score"] is None


def test_hostile_payloads_degrade_safely():
    f = _verdict_facts({"evidence": "junk", "security_checks": "junk", "holder_analysis": None})
    assert f["flagged_security_checks"] == {}
    assert f["threats"] == []


def test_threats_capped_at_six():
    result = {"threats": [f"T{i}" for i in range(10)]}
    f = _verdict_facts(result)
    assert len(f["threats"]) == 6


def test_flagged_security_checks_only_fail_or_pass():
    result = {
        "security_checks": {
            "PASS1": {"status": "pass"},
            "FAIL1": {"status": "fail"},
            "WARN1": {"status": "warn"},  # excluded
        }
    }
    f = _verdict_facts(result)
    assert set(f["flagged_security_checks"]) == {"PASS1", "FAIL1"}
