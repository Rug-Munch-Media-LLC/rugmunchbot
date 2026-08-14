"""Channel discovery — OSS fail-open no-op.

The private monolith discovers followed channels via a Telethon session
(``client.get_dialogs()``). OSS returns ``[]``; callers fall back to the
env-configured explicit channel list.
"""

from __future__ import annotations

from typing import Any


async def discover_followed_channels(client: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """No-op: no headless client, no discovery."""
    return []


__all__ = ["discover_followed_channels"]
