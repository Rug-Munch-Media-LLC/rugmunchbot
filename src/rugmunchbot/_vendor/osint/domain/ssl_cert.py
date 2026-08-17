# VENDORED 2026-08-17 from rmi-backend/app/osint/domain/ssl_cert.py (Phase 2b — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""SSL certificate source — TLS issuer, validity window, subject alt names."""

from __future__ import annotations

import socket
import ssl
from typing import TYPE_CHECKING, Any

import app.osint.domain as _domain
from rugmunchbot._vendor.osint.domain._common import _finding

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport


def _ssl_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Grab the TLS certificate issuer / validity / SANs on port 443."""
    try:
        cert = _domain._ssl_get_cert(domain, timeout, verify=True)
    except ssl.SSLCertVerificationError as exc:
        # Self-signed / mismatched certs are common on scam sites — surface it.
        _finding(
            report,
            source="ssl",
            category="certificate",
            summary="TLS certificate does not verify (self-signed or invalid)",
            confidence="high",
            verify_error=str(exc),
        )
        try:
            cert = _domain._ssl_get_cert(domain, timeout, verify=False)
        except Exception as exc2:
            report.errors.append(f"ssl: could not read certificate after verification failure: {exc2}")
            return
    except (ssl.SSLError, OSError) as exc:
        report.errors.append(f"ssl: TLS handshake failed: {exc}")
        return

    issuer = _flatten_cert_attrs(cert.get("issuer"))
    sans = _flatten_san(cert.get("subjectAltName"))
    org = issuer.get("organizationName", "")
    cn = issuer.get("commonName", "")
    not_before = cert.get("notBefore")
    not_after = cert.get("notAfter")

    data: dict[str, Any] = {"issuer": issuer, "not_before": not_before, "not_after": not_after}
    if sans:
        data["subject_alt_names"] = sans

    _finding(
        report,
        source="ssl",
        category="certificate",
        summary=f"TLS certificate issued by {org or cn or 'unknown'} (valid until {not_after or 'unknown'})",
        confidence="high",
        **data,
    )


def _ssl_get_cert(domain: str, timeout: int, *, verify: bool) -> dict[str, Any]:
    """Perform a TLS handshake and return the parsed peer certificate."""
    context = ssl.create_default_context() if verify else _unverified_context()
    with (
        socket.create_connection((domain, 443), timeout=timeout) as raw,
        context.wrap_socket(raw, server_hostname=domain) as tls,
    ):
        cert = tls.getpeercert()
    if not cert:
        raise ssl.SSLError("server presented no certificate")
    return cert


def _unverified_context() -> ssl.SSLContext:
    """TLS context that still *reads* the peer cert when verification fails."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _flatten_cert_attrs(items: Any) -> dict[str, str]:
    """Flatten ``getpeercert()`` RDN structure (issuer/subject) to name->value."""
    out: dict[str, str] = {}
    if not isinstance(items, (list, tuple)):
        return out
    for rdn in items:
        if not isinstance(rdn, (list, tuple)):
            continue
        for pair in rdn:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                key, value = pair
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    key, value = value
                if isinstance(key, str) and isinstance(value, str):
                    out[key] = value
    return out


def _flatten_san(items: Any) -> list[str]:
    """Flatten ``subjectAltName`` tuples into ``"TYPE:value"`` strings."""
    out: list[str] = []
    if not isinstance(items, (list, tuple)):
        return out
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            kind, value = item
            if isinstance(kind, str) and isinstance(value, str):
                out.append(f"{kind}:{value}")
    return out
