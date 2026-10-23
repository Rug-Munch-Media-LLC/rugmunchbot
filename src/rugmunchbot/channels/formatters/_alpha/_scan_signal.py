"""Signal lines for a scan card: the evidence a reader needs to judge a token.

Pure rendering only. Split out of ``_alpha.py`` (2026-10-23).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.formatters._primitives import _esc
from rugmunchbot.formatters import short_addr

_MAX_DEPLOYER_ADDR_CHARS = 10


def _scan_signal_lines(item: dict[str, Any]) -> list[str]:
    """The actual-signal line parts for a scans-of-interest card.

    Every posted token must show WHY it is suspicious: holders count,
    liquidity amount, volume, first-25-buyers, bundle/deployer flags.
    Renders each field only when the scan payload actually reported it
    (honest-data rule — never a fabricated 0). Pure — never raises.
    """
    from rugmunchbot.formatters._scan_report_common._fields import (
        _float_or_none,
        _holders_count,
    )

    parts: list[str] = []
    holders = _holders_count(item)
    if holders is not None:
        parts.append(f"\U0001f465 <b>{holders:,}</b> holders")
    liquidity = _float_or_none(item.get("liquidity_usd") or item.get("liquidity"))
    if liquidity is not None:
        parts.append(f"\U0001f4a7 <b>${liquidity:,.0f}</b> liq")
    volume = _float_or_none(item.get("volume_24h"))
    if volume is not None:
        parts.append(f"\U0001f4c8 <b>${volume:,.0f}</b> vol 24h")
    first_buyers = item.get("first_buyers")
    cohort: int | None = None
    if isinstance(first_buyers, dict):
        buyers = first_buyers.get("buyers")
        if isinstance(buyers, list) and buyers:
            cohort = (
                first_buyers.get("limit")
                if isinstance(first_buyers.get("limit"), int)
                else len(buyers)
            )
    elif isinstance(first_buyers, list) and first_buyers:
        cohort = len(first_buyers)
    if cohort:
        parts.append(f"\U0001f6d2 first-<b>{cohort}</b> buyers")
    ba = item.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled") is not None:
        parts.append(
            "\U0001f578\ufe0f bundle <b>DETECTED</b>"
            if ba["is_bundled"]
            else "\U0001f578\ufe0f no bundle"
        )
    deployer_addr = item.get("deployer_address")
    dp = item.get("deployer_profile")
    if not deployer_addr and isinstance(dp, dict):
        deployer_addr = dp.get("address") or dp.get("deployer_address")
    if not deployer_addr:
        legacy_dep = item.get("deployer")
        if isinstance(legacy_dep, str) and len(legacy_dep) >= _MAX_DEPLOYER_ADDR_CHARS:
            deployer_addr = legacy_dep
    if deployer_addr:
        parts.append(
            f"\U0001f468\u200d\U0001f4bb deployer <code>{_esc(short_addr(str(deployer_addr)))}</code>"
        )
    return parts
