"""Thin backend scanner HTTP client — isolated from ``rugmunch_scanner``.

Isolation slice (2026-08-30): the bot used to reach ``rugmunch_scanner``
directly through its ``scanner`` extra — the vendored unified engine ran
IN-PROCESS as the primary ``/scan`` path. Per ARCHITECTURE.md ("bot calls
backend HTTP API only"), every scanner call that has a backend endpoint is
routed through THIS client. The in-process ``_local_v2`` engine is retained
only as a resilient fallback and is flagged [REVIEW] for removal once the
backend is authoritative.

All calls go through the shared ``backend_json_post`` client + the shared
secret header so timeouts/retries stay consistent with the rest of the tool
layer. Every call returns ``None`` on failure so callers degrade to the next
leg of the scan fallback chain — never raises.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import BackendApiError
from rugmunchbot.tools.registry import backend_json_post

logger = get_logger("rmi_bot.tools.scanner.backend")

#: Hard budget for the canonical backend scan (the unified engine bounds
#: itself to ~25s; this is a belt-and-suspenders cap).
SCAN_TIMEOUT = 45.0


async def backend_scan_token(
    address: str, chain: str, *, timeout: float = SCAN_TIMEOUT
) -> dict[str, Any] | None:
    """POST ``/api/v2/scanner/token/scan`` on rmi-backend.

    Returns the unified v2 payload (``status == "ok"``) or ``None`` on any
    failure so the caller can fall through to the local engine / legacy
    scanner. Never raises.
    """
    try:
        data = await backend_json_post(
            "/api/v2/scanner/token/scan",
            json={"address": address, "chain": chain},
            timeout=timeout,
        )
    except BackendApiError as exc:
        logger.warning("backend scan error %s for %s: %s", exc.status_code, address, exc)
        return None
    except Exception as exc:
        logger.warning("backend scan failed for %s: %s", address, str(exc)[:120])
        return None
    if not isinstance(data, dict) or data.get("status") != "ok":
        logger.warning("backend scan non-ok response for %s", address)
        return None
    return data
