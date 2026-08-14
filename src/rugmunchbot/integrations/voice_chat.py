"""Optional voice-chat adapter (fail-open no-op).

The live bot creates/deletes Telegram voice chats via a private helper
(``app.domains.telegram.voice_chat``). OSS ships no-ops: every call returns a
safe "unavailable" result and callers skip gracefully.
"""

from __future__ import annotations

from typing import Any


async def get_voice_chat_status(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    return None


async def create_voice_chat(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    return None


async def end_voice_chat(*args: Any, **kwargs: Any) -> bool:
    return False


__all__ = ["create_voice_chat", "end_voice_chat", "get_voice_chat_status"]
