"""Wave 41 — _security.py contracts (was 15% covered).

Pure-logic evidence flattening + honeypot verdict:

* _security_checks: flattens check map to {code: {status, details}},
  tolerates non-dict details, hostile payloads → {}
* _honeypot_verdict: HP01/HP04 fail or "honeypot=yes" → detected;
  pass+no → none; missing/ambiguous → unknown
"""

from __future__ import annotations

from rugmunchbot.tools.scanner._security import (
    _honeypot_verdict,
    _security_checks,
)


def test_checks_flatten():
    evidence = {
        "security": {
            "checks": {
                "HP01": {"status": "fail", "details": "honeypot: yes"},
                "CR01": {"status": "pass", "details": "mint authority renounced"},
            }
        }
    }
    flat = _security_checks(evidence)
    assert flat["HP01"] == {"status": "fail", "details": "honeypot: yes"}
    assert flat["CR01"]["status"] == "pass"


def test_checks_non_dict_details_stringified():
    evidence = {"security": {"checks": {"XX01": "plain string detail"}}}
    flat = _security_checks(evidence)
    assert flat["XX01"] == {"status": None, "details": "plain string detail"}


def test_checks_hostile_payloads_empty():
    assert _security_checks({}) == {}
    assert _security_checks({"security": None}) == {}
    assert _security_checks({"security": {"checks": "junk"}}) == {}


def test_honeypot_detected_on_fail():
    ev = {"security": {"checks": {"HP01": {"status": "fail", "details": ""}}}}
    assert _honeypot_verdict(ev) == "detected"


def test_honeypot_detected_on_details_yes():
    ev = {"security": {"checks": {"HP04": {"status": "warn", "details": "honeypot: yes"}}}}
    assert _honeypot_verdict(ev) == "detected"


def test_honeypot_none_on_pass_no():
    ev = {"security": {"checks": {"HP01": {"status": "pass", "details": "honeypot: no"}}}}
    assert _honeypot_verdict(ev) == "none"


def test_honeypot_unknown_cases():
    assert _honeypot_verdict({}) == "unknown"
    assert _honeypot_verdict({"security": {"checks": {}}}) == "unknown"
    amb = {"security": {"checks": {"HP01": {"status": "warn", "details": "unclear"}}}}
    assert _honeypot_verdict(amb) == "unknown"
