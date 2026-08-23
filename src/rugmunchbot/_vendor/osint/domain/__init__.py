# VENDORED 2026-08-23 from rmi-backend/app/osint/domain/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Domain OSINT investigation — WHOIS / DNS / subdomains / SSL / web archive.

Investigates a domain name (e.g. a scam landing page) across independent
sources and returns a unified :class:`app.osint.OsintReport`:

* **WHOIS** — registrant, registrar, creation/expiration dates, nameservers
  (:mod:`app.osint.domain.whois`). Uses ``python-whois`` when installed,
  falling back to the ``whois`` CLI.
* **DNS** — A / AAAA / MX / NS / TXT / CNAME records via ``dnspython``
  (:mod:`app.osint.domain.dns_records`).
* **Subdomains** — certificate-transparency names from the crt.sh JSON API
  (:mod:`app.osint.domain.subdomains`).
* **SSL certificate** — issuer, validity window, subject alternative names
  (:mod:`app.osint.domain.ssl_cert`).
* **Web archive** — first/last Wayback Machine snapshot and capture count
  (:mod:`app.osint.domain.web_archive`).
* **Tech fingerprint** — HTTP status, final URL, ``Server`` and security
  headers via ``httpx`` (:mod:`app.osint.domain.fingerprint`).
* **theHarvester** — passive email / hostname harvesting from public search
  engines (:mod:`app.osint.domain.theharvester`; optional — skipped when not
  installed).
* **Amass** — passive DNS/subdomain mapping plus address / ASN attribution
  (:mod:`app.osint.domain.amass`; optional — skipped when not installed).

Input normalization lives in :mod:`app.osint.domain.normalize`; shared
constants/helpers in :mod:`app.osint.domain._common`.

Contract (see :mod:`app.osint`): ``lookup_domain(domain, *, timeout=60) ->
OsintReport``.

Error handling — import-safe, per-source degradation:
  * Missing optional dependency (``httpx``, ``dnspython``, ``python-whois``)
    -> a line in ``report.errors``; the module still imports and the other
    sources still run.
  * A failed source (timeout, network error, parse error) -> a line in
    ``report.errors``; the lookup never raises for a source failure.
  * Optional CLI tools (``theHarvester``, ``amass``) missing or failing ->
    a line logged via :mod:`logging`; the source is skipped, never raises,
    and never touches ``report.errors`` (WHOIS/DNS/SSL still produce a
    complete report).
  * Garbage input (scheme/path/port/IP, unparsable hostname) -> ``ValueError``
    before any network I/O.

Usage::

    from rugmunchbot._vendor.osint.domain import lookup_domain

    report = lookup_domain("https://scam-example.com/order", timeout=45)
    print(report.found, report.errors)
    for finding in report.findings:
        print(finding.source, finding.category, finding.summary, finding.confidence)
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess  # noqa: F401  (kept importable as domain.subprocess — tests monkeypatch subprocess.run here)
import time

from rugmunchbot._vendor.osint import OsintReport
from rugmunchbot._vendor.osint.domain import (
    amass,
    dns_records,
    fingerprint,
    ssl_cert,
    subdomains,
    theharvester,
    web_archive,
    whois,
)
from rugmunchbot._vendor.osint.domain.amass import _amass_lookup
from rugmunchbot._vendor.osint.domain.dns_records import _dns_lookup
from rugmunchbot._vendor.osint.domain.fingerprint import _fingerprint_lookup
from rugmunchbot._vendor.osint.domain.normalize import normalize_domain
from rugmunchbot._vendor.osint.domain.ssl_cert import _ssl_get_cert, _ssl_lookup
from rugmunchbot._vendor.osint.domain.subdomains import _crt_lookup, _extract_subdomains
from rugmunchbot._vendor.osint.domain.theharvester import _theharvester_lookup
from rugmunchbot._vendor.osint.domain.web_archive import _wayback_iso, _wayback_lookup
from rugmunchbot._vendor.osint.domain.whois import (
    _emit_whois,
    _fmt_whois_dates,
    _parse_whois_cli,
    _python_whois,
    _whois_lookup,
)

logger = logging.getLogger(__name__)

# ─── Optional dependency detection (import-safe, per-source degradation) ────
#: Whether httpx is importable (crt.sh / Wayback / fingerprint sources).
_HTTPX_AVAILABLE: bool = importlib.util.find_spec("httpx") is not None
#: Whether dnspython is importable (DNS source).
_DNS_AVAILABLE: bool = importlib.util.find_spec("dns") is not None
#: Whether python-whois is importable (WHOIS source; CLI fallback otherwise).
_WHOIS_AVAILABLE: bool = importlib.util.find_spec("whois") is not None

# ─── Re-exports (previous module API surface) ────────────────────────
# The test suite imports the private helpers below by name and monkeypatches
# several of them ON THIS PACKAGE (``_WHOIS_AVAILABLE``, ``_python_whois``,
# ``_ssl_get_cert``, ...). The per-source modules read the monkeypatched ones
# through this package object, so the patches keep taking effect.


# ─── Entry point ─────────────────────────────────────────────────────


def lookup_domain(domain: str, *, timeout: int = 60) -> OsintReport:
    """Investigate a domain across WHOIS, DNS, subdomains, SSL, and archive,
    plus the optional theHarvester and Amass CLI harvesters.

    Args:
        domain: Domain or URL to investigate (scheme/path/port are stripped).
        timeout: Total budget in seconds for the whole lookup, shared by all
            sources; each source gets the remaining budget when it runs.

    Returns:
        An :class:`OsintReport` with one finding per resolved fact and one
        error string per failed/missing source. Never raises for source
        failures; raises :class:`ValueError` only for invalid input.
    """
    if timeout < 1:
        raise ValueError("timeout must be >= 1")
    target = normalize_domain(domain)
    report = OsintReport(target=target, target_type="domain")
    logger.debug("domain OSINT lookup for %s (timeout=%ss)", target, timeout)

    deadline = time.monotonic() + timeout

    def _budget() -> int:
        return max(1, int(deadline - time.monotonic()))

    dns_records._dns_lookup(target, _budget(), report)
    whois._whois_lookup(target, _budget(), report)
    ssl_cert._ssl_lookup(target, _budget(), report)
    subdomains._crt_lookup(target, _budget(), report)
    web_archive._wayback_lookup(target, _budget(), report)
    fingerprint._fingerprint_lookup(target, _budget(), report)
    # Optional passive harvesters run last with whatever budget remains; a
    # missing binary or failure is logged and skipped (see module docstring).
    theharvester._theharvester_lookup(target, _budget(), report)
    amass._amass_lookup(target, _budget(), report)
    return report


__all__ = [
    "_DNS_AVAILABLE",
    "_HTTPX_AVAILABLE",
    "_WHOIS_AVAILABLE",
    "_amass_lookup",
    "_crt_lookup",
    "_dns_lookup",
    "_emit_whois",
    "_extract_subdomains",
    "_fingerprint_lookup",
    "_fmt_whois_dates",
    "_parse_whois_cli",
    "_python_whois",
    "_ssl_get_cert",
    "_ssl_lookup",
    "_theharvester_lookup",
    "_wayback_iso",
    "_wayback_lookup",
    "_whois_lookup",
    "lookup_domain",
    "normalize_domain",
]
