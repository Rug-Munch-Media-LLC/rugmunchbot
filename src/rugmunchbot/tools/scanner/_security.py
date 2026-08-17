"""Scanner engine — evidence security-check flattening + honeypot verdict."""

from __future__ import annotations

from typing import Any


def _security_checks(evidence: dict[str, Any]) -> dict[str, Any]:
    """Flatten the evidence security check map (HP01 honeypot, CR mintable…)."""
    security = evidence.get("security")
    if not isinstance(security, dict):
        return {}
    checks = security.get("checks")
    if not isinstance(checks, dict):
        return {}
    flat: dict[str, Any] = {}
    for code, detail in checks.items():
        if isinstance(detail, dict):
            flat[str(code)] = {
                "status": detail.get("status"),
                "details": str(detail.get("details") or ""),
            }
        else:
            flat[str(code)] = {"status": None, "details": str(detail or "")}
    return flat


def _honeypot_verdict(evidence: dict[str, Any]) -> str:
    """Honeypot verdict from the evidence security checks (HP01 = honeypot)."""
    checks = _security_checks(evidence)
    hp = checks.get("HP01") or checks.get("HP04")
    if not isinstance(hp, dict):
        return "unknown"
    details = str(hp.get("details") or "").lower()
    status = hp.get("status")
    if status == "fail" or "honeypot=yes" in details or "honeypot: yes" in details:
        return "detected"
    if status == "pass" and "no" in details:
        return "none"
    return "unknown"
