"""Optional headless Telegram client adapter (fail-open).

The live bot reads public channels via a private Telethon MTProto client
(``app.domains.telegram.headless``). OSS ships a no-op: ``get_headless_client``
returns ``None`` and every sweep degrades to the explicit channel list.
"""

from __future__ import annotations

from typing import Any


async def get_headless_client() -> Any | None:
    """Return a headless Telethon client, or ``None`` (never raises)."""
    return None


_client_instance: Any | None = None


__all__ = ["_client_instance", "get_headless_client"]
