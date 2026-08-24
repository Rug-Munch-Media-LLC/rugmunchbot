# VENDORED 2026-08-24 from rmi-backend/app/osint/spiderfoot_client/_core.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""SpiderFoot OSINT automation engine client.

Runs a scan against a self-hosted SpiderFoot instance (``infra/spiderfoot/``)
and returns a unified :class:`app.osint.OsintReport`.

SpiderFoot is the general OSINT backbone of the section: given a domain,
email, username, wallet or IP it fans out across 100+ modules (DNS/WHOIS,
certificate-transparency logs, breach feeds, social/profile enumeration,
blockchain, …) and we map its per-event results onto our report model.

Package layout (mirrors :mod:`app.osint.domain` / :mod:`app.osint.face`):

* :mod:`app.osint.spiderfoot_client._core` — public entry point
  (:func:`lookup`) + scan lifecycle orchestration.
* :mod:`app.osint.spiderfoot_client._api` — low-level web-UI API calls
  (start / status / results / errors) + environment resolution.
* :mod:`app.osint.spiderfoot_client._mapping` — event-type -> finding mapping.

Error handling — graceful by design:

    * Server unreachable / connection refused / HTTP error at start
      -> raises :class:`app.osint.OsintNotAvailable`.
    * Scan ends in ERROR-FAILED / ABORTED, or the poll budget runs out
      -> the report is returned with a line in ``report.errors`` and any
      results already collected.
    * A module-level failure inside SpiderFoot (visible via ``/scanerrors``)
      -> appended to ``report.errors``; the rest of the scan is unaffected.

Contract (see :mod:`app.osint`): ``lookup(target, target_type="domain", *,
timeout=120) -> OsintReport``.

Usage::

    from rugmunchbot._vendor.osint.spiderfoot_client import lookup

    report = lookup("example.com", timeout=120)
    print(report.found, report.errors)
    for finding in report.findings:
        print(finding.source, finding.category, finding.summary, finding.confidence)
"""

from __future__ import annotations

import logging
import os
import time

import httpx

from rugmunchbot._vendor.osint import OsintFinding, OsintNotAvailable, OsintReport
from rugmunchbot._vendor.osint.spiderfoot_client._api import (
    _POLL_INTERVAL_SECONDS,
    _RUNNING_STATUSES,
    _TERMINAL_STATUSES,
    _env_url,
    _scan_errors,
    _scan_results,
    _scan_status,
    _start_scan,
)
from rugmunchbot._vendor.osint.spiderfoot_client._mapping import _dedupe_findings, _row_to_finding

logger = logging.getLogger(__name__)

__all__ = ["lookup"]

# ─── Tunables ────────────────────────────────────────────────────────

#: Module group used when the caller does not pin an explicit module list.
#: "Passive" = the safe default set (199 modules that only consult third-party
#: data sources; no active port scans / direct target interaction).
DEFAULT_USECASE = "Passive"


def _env_usecase() -> str:
    """Resolve the module-group use case (safe default: Passive)."""
    usecase = os.environ.get("SPIDERFOOT_USECASE", DEFAULT_USECASE).strip()
    if not usecase:
        return DEFAULT_USECASE
    # The web UI matches against capitalized group names ("Passive", ...).
    if usecase.lower() == "all":
        return "all"
    return usecase[0].upper() + usecase[1:]


# ─── Entry point ─────────────────────────────────────────────────────


def lookup(target: str, target_type: str = "domain", *, timeout: int = 120) -> OsintReport:
    """Run a SpiderFoot scan against ``target`` and map results to a report.

    Args:
        target: The seed target (domain, email, username, wallet, IP, ...).
            SpiderFoot auto-detects the target type from the value.
        target_type: Our report label (domain | email | username | wallet |
            phone | ip). Used only for the report; the engine detects its own.
        timeout: Total budget in seconds for the *polling* phase, shared with
            the API requests. If a scan is still running when the budget runs
            out, the report is returned with whatever results arrived so far
            and a line in ``errors``.

    Returns:
        An :class:`OsintReport` with one finding per SpiderFoot event and one
        error string per failed module / poll timeout. Raises
        :class:`app.osint.OsintNotAvailable` only when SpiderFoot itself is
        unreachable or rejects the scan start (HTTP error, bad config).
    """
    if timeout < 1:
        raise ValueError("timeout must be >= 1")
    if not target or not target.strip():
        raise ValueError("target must not be empty")

    target = target.strip()
    report = OsintReport(target=target, target_type=target_type)
    base_url = _env_url()
    usecase = _env_usecase()

    try:
        with httpx.Client(base_url=base_url, timeout=max(10, timeout)) as client:
            try:
                scan_id = _start_scan(client, target, scanname=target[:64] or target, usecase=usecase)
            except (httpx.HTTPError, RuntimeError) as exc:
                if isinstance(exc, httpx.HTTPError):
                    raise OsintNotAvailable(f"SpiderFoot unreachable at {base_url}: {exc}") from exc
                # Scan rejected for a config reason — surface as unavailable-ish
                raise OsintNotAvailable(str(exc)) from exc

            deadline = time.monotonic() + timeout
            status = "STARTING"
            while status not in _TERMINAL_STATUSES:
                if time.monotonic() >= deadline:
                    report.errors.append(
                        f"spiderfoot: scan {scan_id} still running after {timeout}s (status {status}); "
                        "returning partial results"
                    )
                    break
                try:
                    status, _ = _scan_status(client, scan_id)
                except httpx.HTTPError as exc:
                    report.errors.append(f"spiderfoot: status poll failed: {exc}")
                    break
                if status not in _RUNNING_STATUSES and status not in _TERMINAL_STATUSES:
                    # Unknown state — treat as still going but log it.
                    logger.warning("spiderfoot: unknown scan status %r for scan %s", status, scan_id)
                    if time.monotonic() >= deadline:
                        break
                time.sleep(_POLL_INTERVAL_SECONDS)

            # Always attempt to collect whatever results exist, even on abort.
            try:
                rows = _scan_results(client, scan_id)
            except httpx.HTTPError as exc:
                report.errors.append(f"spiderfoot: could not fetch results: {exc}")
                rows = []

            findings: list[OsintFinding] = []
            for row in rows:
                finding = _row_to_finding(row)
                if finding is not None:
                    findings.append(finding)
            report.findings.extend(_dedupe_findings(findings))

            report.errors.extend(_scan_errors(client, scan_id))

            if status == "ERROR-FAILED":
                report.errors.append(f"spiderfoot: scan {scan_id} ended with ERROR-FAILED")
            elif status == "ABORTED":
                report.errors.append(f"spiderfoot: scan {scan_id} was aborted")
    except OsintNotAvailable:
        raise
    except httpx.HTTPError as exc:
        raise OsintNotAvailable(f"SpiderFoot unreachable at {base_url}: {exc}") from exc
    except Exception as exc:
        # Keep the contract: any engine-side failure is reported, not raised,
        # unless it was a connectivity problem (handled above).
        logger.exception("spiderfoot: unexpected lookup failure")
        report.errors.append(f"spiderfoot: unexpected error: {exc}")

    return report
