"""Merge the scanner-service parity sections onto a bot scan payload.

The bot's vendored enrichment chain stays authoritative for risk scoring; the
parity leg only ADDS ``cohorts`` / ``kol`` / ``impact`` from the standalone
scanner service (see :mod:`rugmunchbot.tools.scanner._parity`). It is
fail-open twice over: never raises, and never spends more than
``PARITY_TIMEOUT`` seconds of a user's scan.

On any failure (URL unset, service down, non-200, non-JSON, non-``ok`` status,
timeout) the payload is returned exactly as the vendored chain left it — no
section keys at all — and ``parity_status`` records ``unavailable``, so the card
renders nothing rather than an em dash storm or a delayed answer.

One public entry point: :func:`apply_parity`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.scanner._parity import PARITY_TIMEOUT, fetch_parity, parity_enabled

logger = get_logger("rmi_bot.tools.scanner.parity_apply")

#: Payload marker for "the parity leg did not answer inside its budget".
UNAVAILABLE = "unavailable"
#: Payload marker for "the three sections were merged".
OK = "ok"


async def apply_parity(data: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Add the three parity sections to ``data`` without ever failing the scan.

    Args:
        data: Scan payload already produced by the vendored enrichment chain.
        address: Token address / mint.
        chain: Chain name as the scanner service spells it (``solana``).

    Returns:
        The same payload, plus ``cohorts`` / ``kol`` / ``impact`` and a
        ``parity_status`` marker. On any failure the payload is unchanged
        apart from ``parity_status='unavailable'``.
    """
    if not parity_enabled():
        data["parity_status"] = UNAVAILABLE
        return data
    try:
        sections = await asyncio.wait_for(fetch_parity(address, chain), timeout=PARITY_TIMEOUT)
    except TimeoutError:
        logger.warning("parity_budget_exceeded token=%s budget=%.0fs", address[:8], PARITY_TIMEOUT)
        data["parity_status"] = UNAVAILABLE
        return data
    except Exception as exc:  # parity must never fail a user scan
        logger.warning("parity_leg_failed token=%s err=%s", address[:8], str(exc)[:120])
        data["parity_status"] = UNAVAILABLE
        return data
    if sections is None:
        logger.warning("parity_unavailable token=%s", address[:8])
        data["parity_status"] = UNAVAILABLE
        return data
    data.update(sections)
    data["parity_status"] = OK
    return data
