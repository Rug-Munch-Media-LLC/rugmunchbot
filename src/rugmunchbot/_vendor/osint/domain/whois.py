# VENDORED 2026-08-24 from rmi-backend/app/osint/domain/whois.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""WHOIS source — registrant + registration details via python-whois / CLI."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Any

import rugmunchbot._vendor.osint.domain as _domain
from rugmunchbot._vendor.osint.domain._common import _finding

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport


def _whois_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Registrant + registration details via python-whois, else the whois CLI."""
    if _domain._WHOIS_AVAILABLE:
        try:
            entry = _domain._python_whois(domain, timeout)
        except Exception as exc:
            report.errors.append(f"whois: python-whois lookup failed: {exc}")
            entry = None
        if entry:
            _emit_whois(report, entry)
            return

    whois_bin = shutil.which("whois")
    if whois_bin:
        try:
            # Fixed argv list (no shell); domain is regex-validated and passed as
            # a single element, so no injection surface — S603 is a false positive.
            result = subprocess.run(  # noqa: S603
                [whois_bin, domain],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except Exception as exc:
            report.errors.append(f"whois: CLI lookup failed: {exc}")
            return
        parsed = _parse_whois_cli(result.stdout or "")
        if not parsed:
            report.errors.append("whois: CLI produced no parseable output")
            return
        _emit_whois(report, parsed)
        return

    report.errors.append("whois: python-whois not installed and no whois CLI available (pip install python-whois)")


def _python_whois(domain: str, timeout: int) -> dict[str, Any]:
    """Run python-whois against the registrable domain (version-agnostic)."""
    import whois as whois_mod

    # python-whois 0.9.x exposes whois(); 0.8.x exposes query().
    func = getattr(whois_mod, "whois", None)
    if callable(func):
        entry = func(domain)
    else:
        query = getattr(whois_mod, "query", None)
        if callable(query):
            entry = query(domain, timeout=timeout)
        else:
            raise RuntimeError("python-whois exposes neither whois() nor query()")
    if isinstance(entry, dict):
        return dict(entry)
    raise RuntimeError(f"unexpected python-whois result type: {type(entry).__name__}")


def _parse_whois_cli(text: str) -> dict[str, Any]:
    """Minimal text parse of the ``whois`` CLI output for key fields."""
    out: dict[str, Any] = {}
    patterns = {
        "registrar": r"^\s*Registrar:\s*(.+)$",
        "creation_date": r"^\s*Creation Date:\s*(.+)$",
        "expiration_date": r"^\s*(?:Registry Expiry Date|Expiration Date|Registrar Registration Expiration Date):\s*(.+)$",
        "updated_date": r"^\s*Updated Date:\s*(.+)$",
        "name": r"^\s*Registrant Name:\s*(.+)$",
        "org": r"^\s*Registrant Organization:\s*(.+)$",
        "country": r"^\s*Registrant Country:\s*(.+)$",
        "dnssec": r"^\s*DNSSEC:\s*(.+)$",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match and match.group(1).strip():
            out[key] = match.group(1).strip()
    nameservers = re.findall(r"^\s*Name Server:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if nameservers:
        out["name_servers"] = [ns.strip() for ns in nameservers]
    status = re.findall(r"^\s*Status:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if status:
        out["status"] = [s.strip() for s in status]
    return out


def _emit_whois(report: OsintReport, data: dict[str, Any]) -> None:
    """Turn a whois result (python-whois dict or CLI parse) into findings."""
    registrant: dict[str, Any] = {}
    for key in ("name", "org", "country", "address", "city", "state", "registrant_postal_code"):
        value = data.get(key)
        if value not in (None, ""):
            registrant[key] = value
    emails = data.get("emails")
    if emails:
        registrant["emails"] = emails if isinstance(emails, list) else [emails]
    if registrant:
        _finding(
            report,
            source="whois",
            category="registrant",
            summary="registrant information",
            confidence="medium",
            **registrant,
        )

    extra: dict[str, Any] = {}
    registrar = data.get("registrar")
    created = _fmt_whois_dates(data.get("creation_date"))
    expires = _fmt_whois_dates(data.get("expiration_date"))
    updated = _fmt_whois_dates(data.get("updated_date"))
    nameservers = data.get("name_servers")
    status = data.get("status")
    dnssec = data.get("dnssec")

    if registrar:
        extra["registrar"] = registrar
    if created:
        extra["creation_date"] = created
    if expires:
        extra["expiration_date"] = expires
    if updated:
        extra["updated_date"] = updated
    if nameservers:
        extra["name_servers"] = nameservers if isinstance(nameservers, list) else [str(nameservers)]
    if status:
        extra["status"] = status if isinstance(status, list) else [str(status)]
    if dnssec:
        extra["dnssec"] = dnssec
    if not extra:
        return

    bits = []
    if registrar:
        bits.append(f"registrar {registrar}")
    if created:
        bits.append(f"created {created}")
    if expires:
        bits.append(f"expires {expires}")
    _finding(
        report,
        source="whois",
        category="registration",
        summary="; ".join(bits) if bits else "registration details",
        confidence="high",
        **extra,
    )


def _fmt_whois_dates(value: Any) -> Any:
    """Normalize whois datetime values (single or list) to ISO strings."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        formatted = [_fmt_whois_dates(item) for item in value]
        return [item for item in formatted if item is not None] or None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
