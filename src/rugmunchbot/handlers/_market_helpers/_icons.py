"""Token icon slots for trending cards — link only, never a fetch.

Telegram cannot inline a bitmap into a text message, so the bot does not
download icons. A token that already carries a usable logo URL (DexScreener
``info.imageUrl``, RugCharts ``icon`` / ``logo``) renders a tappable 🪙
anchor; a token without one renders the plain glyph — the safe fallback, no
broken image and no extra HTTP request. URLs are never synthesised: anything
that is not an http(s) URL is dropped.
"""

from __future__ import annotations

import html
import re
from typing import Any

_ICON_KEYS = ("imageUrl", "image_url", "icon", "icon_url", "logo", "logo_url", "logoURI")
_ICON_URL = re.compile(r"^https?://[^\s<>\"']{4,300}$", re.IGNORECASE)
_FALLBACK_GLYPH = "\U0001fa99"  # 🪙


def _token_icon(token: dict[str, Any]) -> str | None:
    """First valid http(s) icon URL carried by ``token`` (None when absent)."""
    candidates: list[Any] = [token.get(key) for key in _ICON_KEYS]
    info = token.get("info")
    if isinstance(info, dict):
        candidates.extend(info.get(key) for key in _ICON_KEYS)
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        url = raw.strip()
        if url and _ICON_URL.match(url):
            return url
    return None


def _icon_slot(token: dict[str, Any]) -> str:
    """HTML icon slot: tappable 🪙 when a logo exists, plain glyph otherwise."""
    icon = _token_icon(token)
    if icon is None:
        return _FALLBACK_GLYPH
    return f'<a href="{html.escape(icon, quote=True)}">{_FALLBACK_GLYPH}</a>'
