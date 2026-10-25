"""Channel links block for /help (config.CHANNELS-backed).

Split out of ``_help_text.py`` (2026-10-25) — that module is grandfathered
over the 200-line cap and may only shrink.
"""

from __future__ import annotations

import html

from rugmunchbot.config import CHANNELS, TELEGRAM_CHANNEL
from rugmunchbot.formatters import thin_sep

# ── Community channels surfaced in /help (config.CHANNELS-backed) ──────────

CHANNEL_LABELS: dict[str, str] = {
    "main": "Main",
    "news": "News",
    "alerts": "Alerts",
    "alpha": "Alpha",
    "scans": "Scans",
    "whale": "Whale",
}


def _channel_url(key: str, chat_id: object) -> str | None:
    """Resolve one ``config.CHANNELS`` entry to a Telegram link.

    ``alerts`` uses the verified public handle (``TELEGRAM_CHANNEL``);
    ``alpha`` is the ``@rmialpha`` public handle. Remaining channels fall
    back to the ``https://t.me/c/<id>`` deep link derived from their numeric
    chat id. Returns ``None`` when no link can be resolved.
    """
    if key == "alerts" and TELEGRAM_CHANNEL:
        return TELEGRAM_CHANNEL
    if key == "alpha":
        return "https://t.me/rmialpha"
    numeric = str(chat_id or "").strip()
    if numeric.startswith("-100") and numeric[4:].isdigit():
        return f"https://t.me/c/{numeric[4:]}"
    return None


def _channels_section() -> str:
    """Render the community-channels block for /help (HTML-safe)."""
    entries: list[str] = []
    for key, chat_id in CHANNELS.items():
        url = _channel_url(key, chat_id)
        if url is None:
            continue
        label = CHANNEL_LABELS.get(key, key.capitalize())
        entries.append(f'\u2022 <a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
    if not entries:
        return ""
    return f"\U0001f4e2 <b>Channels</b>\n{thin_sep()}\n" + "\n".join(entries)


__all__ = ["CHANNEL_LABELS", "_channel_url", "_channels_section"]
