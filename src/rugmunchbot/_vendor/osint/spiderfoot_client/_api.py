# VENDORED 2026-08-17 from rmi-backend/app/osint/spiderfoot_client/_api.py (Phase 2b — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Low-level SpiderFoot web-UI API calls.

Thin wrapper over the self-hosted SpiderFoot JSON endpoints (OSS v4.0.0 —
**not** the HX ``/rest/v1`` API, which is a paid cloud feature; the self-hosted
web UI exposes these JSON endpoints when the request carries
``Accept: application/json``):

    POST /startscan         -> ["SUCCESS", scanId] | ["ERROR", msg]
    GET  /scanstatus?id=..  -> [name, target, created, started, ended, status, riskmatrix]
    GET  /scaneventresults?id=..&eventType=ALL
                           -> [[lastseen, data, source_data, module, confidence,
                               visibility, risk, hash, fp, parent_fp, event_type], ...]
    GET  /scanerrors?id=..  -> [error strings]

Environment (resolved here, not in the orchestration layer):

    SPIDERFOOT_URL      base URL of the SpiderFoot instance
                        (default ``http://127.0.0.1:5001``)
    SPIDERFOOT_TOKEN    optional bearer token sent as ``Authorization: Bearer``
                        (forward-compat / reverse-proxy auth; the OSS web UI
                        itself has no token auth — keep it loopback-bound)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

#: Base URL of the self-hosted SpiderFoot instance.
DEFAULT_SPIDERFOOT_URL = "http://127.0.0.1:5001"


def _env_url() -> str:
    """Resolve the SpiderFoot base URL from the environment."""
    return os.environ.get("SPIDERFOOT_URL", DEFAULT_SPIDERFOOT_URL).rstrip("/")


def _env_token() -> str | None:
    """Resolve the optional bearer token from the environment."""
    token = os.environ.get("SPIDERFOOT_TOKEN", "").strip()
    return token or None


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Any:
    """Perform a JSON API call against the SpiderFoot web UI."""
    headers = {"Accept": "application/json"}
    token = _env_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.request(method, path, params=params, data=data, headers=headers)
    response.raise_for_status()
    return response.json()


def _as_list(value: Any) -> list[Any]:
    """Coerce an API response to a list.

    Every endpoint this client uses (startscan / scanstatus /
    scaneventresults / scanerrors) returns a JSON array. Non-list responses
    (unexpected server behaviour) are treated as empty.
    """
    if isinstance(value, list):
        narrowed: list[Any] = value
        return narrowed
    return []


def _start_scan(client: httpx.Client, target: str, scanname: str, usecase: str) -> str:
    """Start a scan and return its scan ID."""
    payload = {
        "scanname": scanname,
        "scantarget": target,
        "modulelist": "",
        "typelist": "",
        "usecase": usecase,
    }
    result = _request_json(client, "POST", "/startscan", data=payload)
    # SpiderFoot answers ["SUCCESS", scanId] or ["ERROR", msg].
    items = _as_list(result)
    if len(items) < 2 or items[0] != "SUCCESS":
        message = items[1] if len(items) > 1 else "unknown error"
        raise RuntimeError(f"SpiderFoot failed to start scan: {message}")
    scan_id = items[1]
    if not isinstance(scan_id, str) or not scan_id:
        raise RuntimeError("SpiderFoot returned an empty scan ID")
    return scan_id


def _scan_status(client: httpx.Client, scan_id: str) -> tuple[str, str]:
    """Return ``(status, target)`` for a scan; empty tuple on unknown scan."""
    result = _request_json(client, "GET", "/scanstatus", params={"id": scan_id})
    # [name, target, created, started, ended, status, riskmatrix]
    items = _as_list(result)
    if len(items) < 6:
        return "", ""
    status = items[5]
    target = items[1]
    return (str(status) if status else "", str(target) if target else "")


def _scan_results(client: httpx.Client, scan_id: str) -> list[list[Any]]:
    """Fetch all event rows for a scan (raw list-of-lists)."""
    result = _request_json(client, "GET", "/scaneventresults", params={"id": scan_id, "eventType": "ALL"})
    rows: list[list[Any]] = []
    for row in _as_list(result):
        if isinstance(row, list):
            entry: list[Any] = row
            rows.append(entry)
    return rows


def _scan_errors(client: httpx.Client, scan_id: str) -> list[str]:
    """Fetch module-level error messages for a scan."""
    try:
        result = _request_json(client, "GET", "/scanerrors", params={"id": scan_id})
    except Exception as exc:
        logger.warning("spiderfoot: could not fetch scan errors: %s", exc)
        return []
    messages: list[str] = []
    for item in _as_list(result):
        text = str(item).strip()
        if text and text not in messages:
            messages.append(text)
    return messages[:100]


#: Poll interval while waiting for a scan to finish.
_POLL_INTERVAL_SECONDS: Final = 5.0

#: Scan statuses that mean "done" (success or failure).
_TERMINAL_STATUSES: frozenset[str] = frozenset({"FINISHED", "ERROR-FAILED", "ABORTED"})

#: Statuses the scan is still working in (poll again).
_RUNNING_STATUSES: frozenset[str] = frozenset({"INITIALIZING", "STARTING", "STARTED", "RUNNING"})
