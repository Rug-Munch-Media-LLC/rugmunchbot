# VENDORED 2026-08-23 from rmi-backend/app/osint/domain/subdomains.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Subdomains source — certificate-transparency names from the crt.sh JSON API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import rugmunchbot._vendor.osint.domain as _domain
from rugmunchbot._vendor.osint.domain._common import _CRT_ROW_LIMIT, _HTTP_HEADERS, _finding

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport


def _crt_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Query crt.sh for certificate-transparency subdomain names."""
    if not _domain._HTTPX_AVAILABLE:
        report.errors.append("crt.sh: httpx not installed (pip install httpx)")
        return
    try:
        import httpx
    except ImportError:
        report.errors.append("crt.sh: httpx not installed (pip install httpx)")
        return

    params = {"q": f"%.{domain}", "output": "json"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
            response = client.get("https://crt.sh/", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        report.errors.append(f"crt.sh: query failed: {exc}")
        return

    if not isinstance(data, list):
        report.errors.append("crt.sh: unexpected response shape")
        return

    subdomains = _extract_subdomains(domain, data)
    if not subdomains:
        _finding(
            report,
            source="crt.sh",
            category="subdomain",
            summary=f"no certificate-transparency subdomains found for {domain}",
            confidence="medium",
        )
        return
    _finding(
        report,
        source="crt.sh",
        category="subdomain",
        summary=f"{len(subdomains)} subdomain(s) found in certificate-transparency logs",
        confidence="high",
        subdomains=subdomains,
    )


def _extract_subdomains(domain: str, entries: list[Any]) -> list[str]:
    """Deduplicate + sort crt.sh names that belong to ``domain``."""
    found: set[str] = set()
    suffix = f".{domain}"
    for entry in entries[:_CRT_ROW_LIMIT]:
        if not isinstance(entry, dict):
            continue
        name_value = entry.get("name_value") or entry.get("common_name") or ""
        for name in str(name_value).splitlines():
            name = name.strip().lower().rstrip(".")
            if name.startswith("*."):
                name = name[2:]
            if name == domain or name.endswith(suffix):
                found.add(name)
    return sorted(found)
