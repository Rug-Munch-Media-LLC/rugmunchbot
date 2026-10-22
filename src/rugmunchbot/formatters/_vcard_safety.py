"""LP / authority words for the scan card's single safety row.

Rule 5: authorities, LP lock/burn, tax and honeypot collapse into ONE row,
and any hard fail (honeypot, live mint, live freeze, sell tax >= 50%) is
reported so the verdict can force AVOID regardless of any score.

One public entry point: :func:`safety_row`.
"""

from __future__ import annotations

import contextlib
from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import (
    _freeze_auth,
    _lp_lock_pct,
    _mint_auth,
)
from rugmunchbot.formatters._vcard_fields import DASH, source_for

#: Sell tax at or above this fraction is treated as an exit trap.
SELL_TAX_HARD_FAIL = 0.5

def _authority(value: bool | None, on: str, off: str) -> str:
    """Authority word, ``—`` when the scan never read it."""
    return on if value is True else off if value is False else DASH


def _lock_word(r: dict[str, Any]) -> str:
    """LP lock/burn word — burn beats a percentage, ``—`` when unread."""
    lock = _lp_lock_pct(r)
    if isinstance(lock, (int, float)):
        return f"{lock:.0f}% locked"
    lp = r.get("liquidity_verification")
    if isinstance(lp, dict) and lp.get("lp_burned") is True:
        return "burned"
    return DASH


def safety_row(r: dict[str, Any], ts: str) -> tuple[str, bool]:
    """Rule 5: the single safety row + whether anything hard-failed."""
    honeypot, mint, freeze = r.get("honeypot"), _mint_auth(r), _freeze_auth(r)
    taxes = []
    for raw in (r.get("buy_tax"), r.get("sell_tax")):
        try:
            taxes.append(f"{float(raw) * 100:.0f}%")
        except (TypeError, ValueError):
            taxes.append(DASH)
    hard = honeypot is True or mint is True or freeze is True
    with contextlib.suppress(TypeError, ValueError):
        hard = hard or float(r.get("sell_tax", 0) or 0) >= SELL_TAX_HARD_FAIL
    words = [
        f"mint {_authority(mint, 'ACTIVE', 'renounced')}",
        f"freeze {_authority(freeze, 'ACTIVE', 'renounced')}",
        f"LP {_lock_word(r)}",
        f"tax {taxes[0]}/{taxes[1]}",
        f"honeypot {_authority(honeypot, 'YES', 'no')}",
    ]
    src = source_for(r, "honeypot")
    joined = " \u00b7 ".join(words)
    return f"Safety    {joined} \u00b7 {src} \u00b7 {ts}", hard
