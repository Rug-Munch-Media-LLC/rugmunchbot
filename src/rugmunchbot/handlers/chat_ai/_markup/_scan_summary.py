"""chat_ai scanner-context summary — grounding line for address questions.

Extracted verbatim from the former single-module ``chat_ai/_markup.py``.
"""

from __future__ import annotations

import contextlib
from typing import Any


def _format_scan_summary(address: str, data: dict[str, Any]) -> str | None:
    """Compact single-line risk summary used to ground an address question.

    Returns ``None`` when the scanner returned no risk score so the caller
    falls through to a plain question.

    2026-08-25 cont12: also carries price / 24h volume / deployer funding
    source — all three are in the same quick-scan payload but were dropped,
    so the LLM grounded on risk flags alone and invented market numbers.
    """
    risk = data.get("risk_score")
    if risk is None:
        return None
    flags = [f"risk_score={int(risk)}"]
    for key in ("is_honeypot", "is_mintable", "is_renounced"):
        val = data.get(key)
        if val is not None:
            flags.append(f"{key}={bool(val)}")
    liq = data.get("liquidity_usd") or 0
    if liq:
        flags.append(f"liquidity=${float(liq):,.0f}")
    holders = data.get("holders")
    if holders:
        flags.append(f"holders={holders}")
    price = data.get("price_usd")
    if price:
        with contextlib.suppress(TypeError, ValueError):
            flags.append(f"price=${float(price):.6g}")
    vol = data.get("volume_24h")
    if vol:
        with contextlib.suppress(TypeError, ValueError):
            flags.append(f"vol24h=${float(vol):,.0f}")
    funded = str(data.get("deployer_funded_by") or "").strip().lower()
    if funded and funded not in ("unknown", "none", "n/a"):
        flags.append(f"deployer_funded_by={funded}")
    return f"Context: token {address} scan shows {', '.join(flags)}"
