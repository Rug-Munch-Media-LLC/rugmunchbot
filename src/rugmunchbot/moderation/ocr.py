"""Optional media OCR hook.

NO OCR ENGINE IS INSTALLED (Pillow decodes images, it does not read text; there
is no pytesseract/tesseract in requirements). The hook is therefore inert until
an operator attaches one with :func:`register_hook`; until then media is
covered by the hash blocklist and the burst heuristic only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("rmi_bot.moderation.ocr")

_HOOK: Callable[[str], Awaitable[str]] | None = None


def register_hook(hook: Callable[[str], Awaitable[str]] | None) -> None:
    """Attach ``async fn(file_unique_id) -> text`` (None detaches)."""
    global _HOOK
    _HOOK = hook


def available() -> bool:
    return _HOOK is not None


async def text_of(media_id: str) -> str:
    """OCR text for one media id, or "" when no hook/failure (fail-open)."""
    if _HOOK is None or not media_id:
        return ""
    try:
        return str(await _HOOK(media_id) or "")[:2000]
    except Exception:
        logger.info("ocr hook failed", exc_info=True)
        return ""


__all__ = ["available", "register_hook", "text_of"]
