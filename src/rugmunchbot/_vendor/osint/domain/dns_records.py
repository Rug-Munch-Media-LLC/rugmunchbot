# VENDORED 2026-08-17 from rmi-backend/app/osint/domain/dns_records.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""DNS source — resolve A/AAAA/MX/NS/TXT/CNAME records via dnspython."""

from __future__ import annotations

from typing import TYPE_CHECKING

import rugmunchbot._vendor.osint.domain as _domain
from rugmunchbot._vendor.osint.domain._common import _DNS_RECORD_TYPES, _finding

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport


def _dns_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Resolve A/AAAA/MX/NS/TXT/CNAME records via dnspython."""
    if not _domain._DNS_AVAILABLE:
        report.errors.append("dns: dnspython not installed (pip install dnspython)")
        return
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        report.errors.append("dns: dnspython not installed (pip install dnspython)")
        return

    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = timeout
    resolver.lifetime = timeout

    for rtype in _DNS_RECORD_TYPES:
        try:
            answers = resolver.resolve(domain, rtype)
        except dns.resolver.NXDOMAIN:
            _finding(
                report,
                source="dns",
                category="dns",
                summary=f"{domain} does not resolve (NXDOMAIN)",
                confidence="high",
            )
            return
        except dns.resolver.NoAnswer:
            continue  # name exists but has no records of this type — normal
        except dns.exception.Timeout:
            report.errors.append(f"dns: {rtype} query timed out")
            continue
        except Exception as exc:  # NoNameservers, network, etc.
            report.errors.append(f"dns: {rtype} lookup failed: {exc}")
            continue

        records = [str(rdata) for rdata in answers]
        if records:
            _finding(
                report,
                source="dns",
                category="dns",
                summary=f"{len(records)} {rtype} record(s)",
                confidence="high",
                record_type=rtype,
                records=records,
            )
