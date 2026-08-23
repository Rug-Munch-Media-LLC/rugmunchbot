# VENDORED 2026-08-23 from rmi-backend/app/osint/domain/_common.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Shared helpers and constants for the domain OSINT package.

Holds pure, immutable state plus the finding-emission helper used by every
per-source module. The *mutable* availability flags (``_HTTPX_AVAILABLE`` /
``_DNS_AVAILABLE`` / ``_WHOIS_AVAILABLE``) deliberately live on the package
(``app.osint.domain.__init__``) because the test suite monkeypatches them
there; the per-source modules read them through the package object so those
patches take effect.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.osint import OsintFinding, OsintReport

#: User-Agent used for the HTTP sources (crt.sh, Wayback, fingerprinting).
_HTTP_HEADERS: dict[str, str] = {"User-Agent": "Mozilla/5.0 (compatible; RugMunch-OSINT/1.0; +https://rugmunch.io)"}

#: Record types queried by the DNS source.
_DNS_RECORD_TYPES: tuple[str, ...] = ("A", "AAAA", "MX", "NS", "TXT", "CNAME")

#: HTTP headers of interest for the tech-fingerprint source (lowercase).
_FINGERPRINT_HEADERS: frozenset[str] = frozenset(
    {
        "server",
        "x-powered-by",
        "x-aspnet-version",
        "content-type",
        "content-language",
        "x-frame-options",
        "strict-transport-security",
    }
)

#: Upper bound of crt.sh rows processed (popular domains return huge JSON).
_CRT_ROW_LIMIT: int = 5000

#: theHarvester search sources (comma-separated, no API keys required; see ``theHarvester -h``).
#: Chosen for speed: the 5-source default finished in ~60s while these three
#: complete in ~7s, which fits the shared lookup budget. Add keyless sources
#: (e.g. ``hudsonrock``) if slower, richer harvests are acceptable.
_THEHARVESTER_SOURCES: str = "crtsh,rapiddns,hackertarget"

#: Per-source result cap passed to theHarvester's ``--limit``.
_THEHARVESTER_LIMIT: int = 50


def _finding(
    report: OsintReport,
    *,
    source: str,
    category: str,
    summary: str,
    confidence: str = "medium",
    **data: Any,
) -> None:
    """Map a resolved fact to an :class:`OsintFinding` and append it."""
    report.findings.append(
        OsintFinding(source=source, category=category, summary=summary, confidence=confidence, data=dict(data))
    )
