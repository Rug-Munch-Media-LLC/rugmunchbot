"""Parity sections (cohorts / KOL / impact) from the scanner service.

The bot's own v2 scan (backend ``/api/v2/scanner/token/scan``, or the vendored
engine as fallback) carries no launch-cohort economics, no KOL flow and no exit
depth. Those three sections already exist in the scanner service
(``rugmunch_scanner/enrichment/attach.py`` → ``build_cohorts`` / ``build_kol`` /
``build_impact``), so the bot reads them from there instead of growing a second
implementation: one source of truth, one place to fix.

Transport: ``POST {SCANNER_SERVICE_URL}/scan`` with ``{token, chain}``, the
route the service actually serves (``service/scan_http.py``). Only the three
parity keys are kept; the rest of the service payload is ignored because the
bot's own scan is authoritative for risk scoring.

Fail-open contract: every failure (URL unset, service down, non-200, non-JSON,
``status != "ok"``) returns ``None`` — a section is never fabricated and never
zero-filled. Sections the service answered ``ok`` but omitted come back as
``{"reason": "service omitted <key>"}`` so the card says so instead of showing
a fake zero.

One public entry point: :func:`fetch_parity`.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.scanner._scanner_direct import scanner_direct_url

logger = get_logger("rmi_bot.tools.scanner.parity")

#: Route the standalone service serves (see ``service/scan_http.py``).
SCAN_PATH = "/scan"
#: Sections copied onto the bot's scan payload, in card order.
PARITY_KEYS = ("cohorts", "kol", "impact")
#: Budget for the whole parity call. The service runs the three builders
#: concurrently and bounds itself, so this only guards a wedged service.
PARITY_TIMEOUT = 15.0


async def fetch_parity(address: str, chain: str) -> dict[str, Any] | None:
    """Fetch ``cohorts`` / ``kol`` / ``impact`` for one token from the service.

    Args:
        address: Token address / mint.
        chain: Chain name as the service registry spells it (``solana``).

    Returns:
        A dict with exactly :data:`PARITY_KEYS` when the service answered
        ``status == "ok"``, else ``None``. Never raises and never blocks a
        user scan beyond :data:`PARITY_TIMEOUT`.
    """
    base = scanner_direct_url()
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(base_url=base, timeout=PARITY_TIMEOUT) as client:
            resp = await client.post(SCAN_PATH, json={"token": address, "chain": chain})
    except httpx.HTTPError as exc:
        logger.warning("parity_fetch_failed token=%s err=%s", address[:8], str(exc)[:120])
        return None
    if resp.status_code != 200:
        logger.warning("parity_fetch_http token=%s status=%s", address[:8], resp.status_code)
        return None
    try:
        body = resp.json()
    except ValueError:
        logger.warning("parity_fetch_json token=%s", address[:8])
        return None
    if not isinstance(body, dict) or body.get("status") != "ok":
        status = body.get("status") if isinstance(body, dict) else None
        logger.warning("parity_fetch_status token=%s status=%r", address[:8], status)
        return None
    return {key: body.get(key) or {"reason": f"service omitted {key}"} for key in PARITY_KEYS}


def parity_enabled() -> bool:
    """True when a service URL is configured (no URL means no parity call)."""
    return bool(
        os.getenv("SCANNER_SERVICE_URL", "").strip() or os.getenv("SCANNER_DIRECT_URL", "").strip()
    )


__all__ = ["PARITY_KEYS", "PARITY_TIMEOUT", "SCAN_PATH", "fetch_parity", "parity_enabled"]
