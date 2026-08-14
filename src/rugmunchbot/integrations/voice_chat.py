"""Optional voice-chat adapter (fail-open no-op).

The live bot creates/deletes Telegram voice chats via a private helper
(``app.domains.telegram.voice_chat``). OSS ships no-ops: every call returns a
safe "unavailable" result and callers skip gracefully.

``VoiceChatStatus`` is a structural stand-in for the live
``VoiceChatSession`` dataclass — only the fields callers actually read are
declared. The no-op always returns ``None``; the Protocol exists so callers
type-check against the real contract.
"""

from __future__ import annotations

from typing import Any, Protocol


class VoiceChatStatus(Protocol):
    """Structural contract for a voice/video chat status object."""

    is_video: bool
    participant_count: int
    duration_seconds: float


async def get_voice_chat_status(*args: Any, **kwargs: Any) -> VoiceChatStatus | None:
    return None


async def create_voice_chat(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    return None


async def end_voice_chat(*args: Any, **kwargs: Any) -> bool:
    return False


__all__ = [
    "VoiceChatStatus",
    "create_voice_chat",
    "end_voice_chat",
    "get_voice_chat_status",
]
