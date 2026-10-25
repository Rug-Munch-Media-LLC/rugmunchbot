"""_local_v2.py split part: scan_token_v2_local."""
from __future__ import annotations
from typing import Any
import asyncio
from .localv2_p1 import _LOCAL_SCAN_TIMEOUT_S, logger  # noqa: F401
from .localv2_p3 import _to_v2_payload, install_import_bridge  # noqa: F401

async def scan_token_v2_local(
    address: str,
    chain: str,
    *,
    deep: bool = False,
) -> dict[str, Any] | None:
    """Run the vendored ``UnifiedTokenScanner`` in-process → v2 payload dict.

    ``deep=True`` maps to the engine's SENTINEL deep tier (``scan_tier``
    ``"deep"``, elite phase tier) — the full free+premium module set the
    vendored fleet supports. Returns the v2-compatible dict or ``None`` when
    the engine produced no usable data (callers fall back to the backend).
    """
    if not address:
        return None
    install_import_bridge()
    try:
        from rugmunch_scanner.unified.unified import get_token_scanner

        scanner = get_token_scanner()
        result = await asyncio.wait_for(
            scanner.scan_token(address=address, chain=chain, tier="deep" if deep else "quick"),
            timeout=_LOCAL_SCAN_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning(
            "local_v2_scan_failed addr=%.40s chain=%s deep=%s err=%s",
            address[:40],
            chain,
            deep,
            str(exc)[:160],
        )
        return None
    return _to_v2_payload(result, address, chain)
