"""Proactive LP-expiry alerts (win 35).

Checks recently-alerted tokens for LP locks approaching expiry.
Posts CRITICAL/WARNING findings to the alerts channel. Fail-open.
"""

from __future__ import annotations

import html
import logging
import os
import time

logger = logging.getLogger(__name__)

_DEDUPE_TTL = float(os.getenv("LP_EXPIRY_ALERT_TTL", "43200"))  # 12h
_seen: dict[str, float] = {}


async def build_lp_expiry_post() -> str | None:
    """Check recent alert tokens for LP expiry risk. None when clear."""
    from rugmunchbot.tools.lp_expiry import check_lp_expiry

    # Get recently flagged tokens from the alerts channel's last sweep.
    # These are the tokens our users actually hold or are watching.
    try:
        from rugmunchbot.channels.scheduler._core import _posted_keys

        alert_keys = _posted_keys.get("alerts", set())
        if not alert_keys:
            return None
        # Extract addresses from dedupe keys (format: "alerts:0x...")
        addresses = [
            k.split(":", 1)[1] if ":" in k else k
            for k in list(alert_keys)[-20:]  # check last 20
            if k.startswith("0x") or "0x" in k
        ]
    except Exception as exc:
        logger.warning("lp_expiry:alert_keys_failed error=%s", str(exc)[:100])
        return None

    if not addresses:
        return None

    now = time.time()
    warnings: list[str] = []
    for addr in addresses[:10]:  # cap batch
        dedupe_key = f"lp_expiry:{addr}"
        if dedupe_key in _seen and now - _seen[dedupe_key] < _DEDUPE_TTL:
            continue
        try:
            from rugmunchbot.tools.lp_expiry import check_lp_expiry

            result = await check_lp_expiry(addr)
            if result.get("status") == "EXPIRING":
                _seen[dedupe_key] = now
                sev = "🚨" if result.get("severity") == "CRITICAL" else "⚠️"
                sym = result.get("symbol") or addr[:10]
                days = result.get("days_remaining", "?")
                pct = result.get("locked_pct", "?")
                warnings.append(
                    f"{sev} <b>{html.escape(str(sym))}</b> — LP lock expires "
                    f"in <b>{days} days</b> ({pct}% locked). Exit window closing."
                )
        except Exception as exc:
            logger.debug("lp_expiry_check failed %s: %s", addr[:10], str(exc)[:80])

    if not warnings:
        return None

    lines = ["⏳ <b>LP Expiry Warning</b>", ""]
    lines.extend(warnings)
    lines.append("")
    lines.append("via @rugmunchbot · rugmunch.io")
    return "\n".join(lines)
