"""System alerts to @rmiadminbot for critical events.

Sends structured alerts when the scanner, indexer, or infrastructure has
issues. Reads ``RMI_ADMIN_CHAT_ID`` from env (the @rmiadminbot chat ID).
Fail-open: if the chat ID is not configured, alerts are logged but not sent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ADMIN_CHAT_ID = os.getenv("RMI_ADMIN_CHAT_ID", "")

_SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}


async def send_system_alert(
    bot: Any,
    severity: str,
    module: str,
    message: str,
    **extra: Any,
) -> None:
    """Send a system alert to @rmiadminbot. Fail-open (never raises)."""
    if not ADMIN_CHAT_ID:
        logger.warning(
            "system_alert_no_admin_chat_id severity=%s module=%s msg=%s",
            severity,
            module,
            message[:100],
        )
        return
    try:
        emoji = _SEVERITY_EMOJI.get(severity, "⚪")
        text = f"{emoji} [{severity.upper()}] {module}\n{message}"
        if extra:
            text += "\n" + "\n".join(f"  {k}: {v}" for k, v in extra.items() if v is not None)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    except Exception as exc:
        logger.warning("system_alert_send_failed err=%s", str(exc)[:100])
