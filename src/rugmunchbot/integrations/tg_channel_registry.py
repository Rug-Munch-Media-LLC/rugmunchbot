"""Telegram channel registry — OSS fail-open no-op.

The live bot loads watched-channel metadata from a private databus registry
(``app.databus.tg_channel_registry``). OSS returns ``[]``; scam_watch falls
back to its env-configured ``RMI_SCAM_WATCH_CHANNELS`` list.
"""

from __future__ import annotations

from typing import Any


def load_tg_channels(**kwargs: Any) -> list[dict[str, Any]]:
    """No-op: no registry in OSS."""
    return []


__all__ = ["load_tg_channels"]
