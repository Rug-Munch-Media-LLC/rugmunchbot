"""NSFW media hook — hash blocklist first, optional classifier second.

Telegram exposes ``file_unique_id`` without a download, so the shipped path is
privacy-safe: we hash the id and compare it with the operator's
``nsfw_hashes`` list. No classifier dependency is installed; a real one can be
attached with :func:`register_hook` (score in 0..1).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("rmi_bot.moderation.nsfw")

THRESHOLD = 0.80
_HOOK: Callable[[str], float | None] | None = None


def register_hook(hook: Callable[[str], float | None] | None) -> None:
    """Attach a classifier ``fn(file_unique_id) -> score`` (None detaches)."""
    global _HOOK
    _HOOK = hook


def digest(media_id: str) -> str:
    """Stable, non-reversible id used in the blocklist."""
    return hashlib.sha256((media_id or "").encode()).hexdigest()[:32]


def hit(media_id: str, policy) -> str | None:
    """Return the reason this media is banned, or None."""
    if not media_id:
        return None
    known = policy.nsfw_hashes or ()
    if digest(media_id) in known:
        return "media hash on the NSFW blocklist"
    if _HOOK is None:
        return None
    try:
        score = _HOOK(media_id)
    except Exception:
        logger.info("nsfw hook failed", exc_info=True)
        return None
    if score is not None and float(score) >= THRESHOLD:
        return f"nsfw classifier score {float(score):.2f}"
    return None


__all__ = ["THRESHOLD", "digest", "hit", "register_hook"]
