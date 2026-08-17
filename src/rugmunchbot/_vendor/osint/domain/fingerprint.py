# VENDORED 2026-08-17 from rmi-backend/app/osint/domain/fingerprint.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Tech fingerprint source — HTTP status, final URL, interesting headers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import rugmunchbot._vendor.osint.domain as _domain
from rugmunchbot._vendor.osint.domain._common import _FINGERPRINT_HEADERS, _HTTP_HEADERS, _finding

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport


def _fingerprint_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Fetch the site and record status, final URL, and interesting headers."""
    if not _domain._HTTPX_AVAILABLE:
        report.errors.append("fingerprint: httpx not installed (pip install httpx)")
        return
    try:
        import httpx
    except ImportError:
        report.errors.append("fingerprint: httpx not installed (pip install httpx)")
        return

    response = None
    last_error: Exception | None = None
    for scheme in ("https", "http"):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
                response = client.get(f"{scheme}://{domain}/")
            break
        except Exception as exc:
            last_error = exc

    if response is None:
        report.errors.append(f"fingerprint: HTTP(S) fetch failed: {last_error or 'unknown error'}")
        return

    picked: dict[str, str] = {}
    for key, value in response.headers.items():
        if key in _FINGERPRINT_HEADERS:
            picked[key] = value

    data: dict[str, Any] = {"status_code": response.status_code, "final_url": str(response.url)}
    if picked:
        data["headers"] = picked
    _finding(
        report,
        source="http",
        category="fingerprint",
        summary=f"HTTP {response.status_code} response, final host {response.url.host or domain}",
        confidence="medium",
        **data,
    )
