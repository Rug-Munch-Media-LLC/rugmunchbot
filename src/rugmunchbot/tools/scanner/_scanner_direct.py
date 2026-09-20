"""Direct scanner-service adapter — feature-flagged stub (SCANNER_DIRECT=0).

The bot currently reaches the scanner through rmi-backend HTTP
(``tools/scanner/_backend.py`` → ``/api/v2/scanner/token/scan``) with the
vendored in-process engine (``tools/scanner/_local_v2.py``) as a resilient
fallback. The fleet split (standards/REPO-BOUNDARIES.md) makes
``rugmunch-scanner`` the owner of detection engines; once that service is
deployed standalone, the bot should call it directly instead of routing
through rmi-backend.

This module is the ADAPTER SEAM for that cutover. It is a stub: with the
flag off (the default) every entry point returns ``None`` so callers fall
through to the existing backend/local chain — zero behaviour change.

Flows that will switch to this adapter once the scanner service exists
--------------------------------------------------------------------
* ``/scan`` and ``/deepscan`` — ``tools/scanner/_scans.py``
  (``scan_token_v2`` / ``_deep_scan_token_v2``) will try
  :func:`direct_scan_token` before the backend HTTP leg.
* ``/wallet`` forensics — ``tools/scanner/_scans.py``
  (``analyze_wallet_v2``) will try :func:`direct_scan_wallet`.
* Scanner alerts — ``handlers/alerts/_jobs.py`` (monitor deltas) and
  ``channels/scanner_alerts.py`` (RugBrain poll) will consume the scanner
  service's own alert rail (``rugmunch_scanner/alerts``) instead of the
  backend webhook queue.

Flag
----
``SCANNER_DIRECT`` (env, default ``"0"``). Truthy values: ``1``/``true``/
``yes``/``on``. Read at call time (not import time) so an operator can flip
it without a code change; the bot is not restarted by this batch.

Contract
--------
Every function returns ``None`` on any failure (flag off, service
unreachable, non-ok payload) so callers degrade to the next leg of the
existing fallback chain — never raises. This mirrors the fail-open contract
of ``tools/scanner/_backend.py``.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.scanner.direct")

#: Env flag name. Default OFF — the adapter is inert until the scanner
#: service is deployed and the operator opts in.
SCANNER_DIRECT_ENV = "SCANNER_DIRECT"

#: Base URL of the future standalone scanner service. Unset by default; the
#: adapter stays inert even when the flag is on until this is configured.
SCANNER_DIRECT_URL_ENV = "SCANNER_DIRECT_URL"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def scanner_direct_enabled() -> bool:
    """True when the direct-scanner adapter is switched on.

    Read at call time so the flag can be flipped without a restart. Default
    ``False`` — the stub is inert and every caller keeps its current path.
    """
    return os.getenv(SCANNER_DIRECT_ENV, "0").strip().lower() in _TRUTHY


def scanner_direct_url() -> str | None:
    """Base URL of the standalone scanner service, or ``None`` when unset."""
    url = os.getenv(SCANNER_DIRECT_URL_ENV, "").strip()
    return url or None


async def direct_scan_token(
    address: str,
    chain: str,
    *,
    deep: bool = False,
) -> dict[str, Any] | None:
    """Token scan via the standalone scanner service (STUB).

    Returns ``None`` until the scanner service exists and the flag is on, so
    ``scan_token_v2`` falls through to the backend HTTP leg unchanged.
    """
    if not scanner_direct_enabled() or scanner_direct_url() is None:
        return None
    logger.debug(
        "scanner_direct_stub token=%s chain=%s deep=%s — service not deployed",
        address[:10],
        chain,
        deep,
    )
    return None


async def direct_scan_wallet(
    address: str,
    chain: str,
) -> dict[str, Any] | None:
    """Wallet forensics via the standalone scanner service (STUB).

    Returns ``None`` until the scanner service exists and the flag is on, so
    ``analyze_wallet_v2`` falls through to the backend HTTP leg unchanged.
    """
    if not scanner_direct_enabled() or scanner_direct_url() is None:
        return None
    logger.debug(
        "scanner_direct_stub wallet=%s chain=%s — service not deployed",
        address[:10],
        chain,
    )
    return None


__all__ = [
    "SCANNER_DIRECT_ENV",
    "SCANNER_DIRECT_URL_ENV",
    "direct_scan_token",
    "direct_scan_wallet",
    "scanner_direct_enabled",
    "scanner_direct_url",
]
