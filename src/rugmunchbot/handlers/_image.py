#!/usr/bin/env python3
"""
RugMunchBot -- token image reply helpers (degodded 2026-08-14).

Extracted from ``handlers/scanning.py`` — ``_maybe_send_token_image`` sends
the token image as a photo reply on scan messages. Fail-open: no image URL
→ text-only reply; a dead image host / Telegram error is logged and
swallowed so the scan reply is never lost.

Re-exported by ``handlers.scanning`` so existing importers/tests keep
working unchanged.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.formatters._sentinel import _token_image_url
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


async def _maybe_send_token_image(msg: Any, result: dict[str, Any]) -> None:
    """Send the token image as a photo reply when the scan has one.

    Reads ``image_uri`` / ``image_url`` / ``logo_url`` off the scan result
    (see ``formatters._sentinel._token_image_url``). Fail-open: no image URL
    → text-only reply, no crash; a dead image host / Telegram error is
    logged and swallowed so the scan reply is never lost.
    """
    url = _token_image_url(result)
    if not url:
        return
    try:
        await msg.reply_photo(
            photo=url, caption=f"\U0001f5bc\ufe0f {result.get('name', '')} ({result.get('symbol', '')})"
        )
    except Exception as e:
        logger.warning("token image send failed, continuing text-only: %s", e)
