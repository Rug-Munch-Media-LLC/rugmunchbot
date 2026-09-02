"""Personal channel env knobs — master switches, cadence, Ghost Admin API.

Extracted verbatim from the former single-module ``channels/personal_channel.py``.
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, min(1.0, float(raw.strip())))
    except (TypeError, ValueError):
        return default


#: Master switch — intentionally default OFF. This posts to the operator's
#: own channel; flipping it on is an explicit operator decision.
PERSONAL_CHANNEL_ENABLED: bool = _env_bool("RMI_PERSONAL_CHANNEL_ENABLED", False)
PERSONAL_CHANNEL_INTERVAL_MINUTES: int = _env_int("RMI_PERSONAL_CHANNEL_INTERVAL_MINUTES", 180)
PERSONAL_CHANNEL_THRESHOLD: float = _env_float("RMI_PERSONAL_CHANNEL_THRESHOLD", 0.7)
PERSONAL_CHANNEL_POST_LIMIT: int = _env_int("RMI_PERSONAL_CHANNEL_POST_LIMIT", 3)
PERSONAL_CHANNEL_GHOST_ENABLED: bool = _env_bool("RMI_PERSONAL_CHANNEL_GHOST_ENABLED", True)

#: Master Ghost cross-post switch — intentionally default OFF. The story
#: router's cross-post stays disabled until the operator opts in AND
#: provisions ``GHOST_ADMIN_API_KEY`` (fail-open: the Telegram post still
#: ships either way).
GHOST_CROSSPOST_ENABLED: bool = _env_bool("RMI_GHOST_CROSSPOST_ENABLED", False)

#: Ghost Admin API config. The URL defaults to the canonical blog; the key is
#: the Admin API key (``<id>:<secret>``). See the module docstring for the
#: fail-open contract.
GHOST_ADMIN_API_URL: str = os.getenv("GHOST_ADMIN_API_URL", "https://blog.rugmunch.io")
GHOST_ADMIN_API_KEY: str = os.getenv("GHOST_ADMIN_API_KEY", "")
