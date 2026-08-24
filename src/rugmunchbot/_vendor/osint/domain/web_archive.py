# VENDORED 2026-08-24 from rmi-backend/app/osint/domain/web_archive.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Web archive source — first/last Wayback Machine snapshot + capture count."""

from __future__ import annotations

from typing import TYPE_CHECKING

import rugmunchbot._vendor.osint.domain as _domain
from rugmunchbot._vendor.osint.domain._common import _HTTP_HEADERS, _finding

if TYPE_CHECKING:
    from rugmunchbot._vendor.osint import OsintReport


def _wayback_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Query the Wayback CDX API for first/last snapshot and capture count."""
    if not _domain._HTTPX_AVAILABLE:
        report.errors.append("web-archive: httpx not installed (pip install httpx)")
        return
    try:
        import httpx
    except ImportError:
        report.errors.append("web-archive: httpx not installed (pip install httpx)")
        return

    params = {"url": domain, "output": "json", "limit": "10"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
            response = client.get("http://web.archive.org/cdx/search/cdx", params=params)
            response.raise_for_status()
            rows = response.json()
    except Exception as exc:
        report.errors.append(f"web-archive: CDX query failed: {exc}")
        return

    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        report.errors.append("web-archive: unexpected CDX response shape")
        return
    header = rows[0]
    try:
        timestamp_index = header.index("timestamp")
    except ValueError:
        report.errors.append("web-archive: CDX response missing timestamp column")
        return

    stamps = [
        row[timestamp_index]
        for row in rows[1:]
        if isinstance(row, list)
        and len(row) > timestamp_index
        and isinstance(row[timestamp_index], str)
        and row[timestamp_index].isdigit()
    ]
    if not stamps:
        _finding(
            report,
            source="web.archive.org",
            category="web-archive",
            summary=f"no archived snapshots found for {domain}",
            confidence="medium",
            snapshot_count=0,
            first_snapshot=None,
            last_snapshot=None,
        )
        return
    _finding(
        report,
        source="web.archive.org",
        category="web-archive",
        summary=f"{len(stamps)} archived snapshot(s); first {_wayback_iso(min(stamps))}, last {_wayback_iso(max(stamps))}",
        confidence="high",
        snapshot_count=len(stamps),
        first_snapshot=_wayback_iso(min(stamps)),
        last_snapshot=_wayback_iso(max(stamps)),
    )


def _wayback_iso(timestamp: str) -> str:
    """Convert a CDX ``YYYYMMDDHHMMSS`` timestamp to an ISO-8601 string."""
    if len(timestamp) == 14 and timestamp.isdigit():
        return f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]}T{timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}Z"
    return timestamp
