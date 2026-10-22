#!/usr/bin/env python3
"""Re-run one scan for the verdict card's in-place Refresh / Full report.

The Refresh button must produce *new* numbers, so it bypasses the 30 s
``/scan`` cache deliberately (``fresh=True``); the Full-report button is
happy with the cached payload the card was rendered from (``fresh=False``),
which keeps a tap cheap.

Composition mirrors ``cmd_scan``'s fetch path exactly (v2 → parity →
deployer/market backfill, legacy scanner as fallback) minus the resolve step
(a Refresh always carries an already-resolved contract address) and minus
the background-phase poll (a refresh card is allowed to show the fast-path
numbers; the card's own timestamps make that honest).

One public entry point: :func:`load_scan`.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters import detect_chain
from rugmunchbot.scanner import apply_parity, scan_token, scan_token_v2, v2_token_to_legacy
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.scanner import (
    _scan_cache_get,
    _scan_cache_set,
    _supplement_deployer_socials,
)

logger = get_logger("rmi_bot.vcard")

__all__ = ["load_scan"]


async def load_scan(
    address: str, chain: str | None = None, *, fresh: bool = False
) -> dict[str, Any] | None:
    """Scan payload for ``address`` — cache-first, or a forced fresh scan.

    Returns ``None`` when no backend answered and the legacy scanner raised
    nothing usable; the caller then renders a failure card instead of an
    invented result.
    """
    chain_key = chain or detect_chain(address)
    if not fresh:
        cached = _scan_cache_get(address, chain_key)
        if cached is not None:
            return cached
    try:
        result_v2 = await scan_token_v2(address, chain)
        if result_v2 is not None:
            result = await apply_parity(v2_token_to_legacy(result_v2), address, chain_key)
        else:
            result = await scan_token(address, chain)
        result = await _supplement_deployer_socials(
            result, address, chain or str(result.get("chain") or "")
        )
    except Exception:
        logger.exception("vcard rescan failed address=%.12s", address)
        return None
    _scan_cache_set(address, chain_key, result)
    return result
