#!/usr/bin/env python3
"""Channel chat-id resolution — gopass → env → documented fallback."""

from __future__ import annotations

from rugmunchbot._vendor.channels_registry import CHANNEL_ROLES

from ._secrets import _get_token

# ══════════════════════════════════════════════
# CHANNELS
# ══════════════════════════════════════════════
# Channel chat ids are resolved with the same gopass → env → fallback pattern
# as BOT_TOKEN (`_get_token`). The canonical channel map (role → gopass path →
# env var → public handle → fallback) lives in app/core/channels_registry.py
# and is SHARED with the backend alert transport
# (app/domains/alerts/telegram_transport.py), so the bot and the backend can
# never drift on which channel a role maps to. The env vars below let
# operators override per-deployment; the documented fallback chat ids keep
# /help links and the poster working when neither gopass nor env is available
# (they are public channel ids, not credentials).
_CHANNEL_GOPASS_PATHS: dict[str, str] = {role: c.store_path for role, c in CHANNEL_ROLES.items()}
_CHANNEL_ENV_KEYS: dict[str, list[str]] = {
    role: [c.env_var, *c.legacy_env_vars] for role, c in CHANNEL_ROLES.items()
}
# Documented fallback chat ids — used ONLY when gopass + env are both unset.
# Roles without a documented fallback (e.g. ``updates``) are not included.
_CHANNEL_DEFAULTS: dict[str, str] = {
    role: c.fallback for role, c in CHANNEL_ROLES.items() if c.fallback is not None
}

_channel_cache: dict[str, str | None] = {}


def _resolve_channel_id(key: str) -> str | None:
    """Resolve one channel chat id: gopass → env → documented fallback.

    Memoized so the periodic poster does not shell out to gopass on every
    tick. ``reset_channel_cache()`` clears the memo (tests / runtime env
    reload). Unknown roles and roles without any resolved value return None.
    """
    if key in _channel_cache:
        return _channel_cache[key]
    gopass_path = _CHANNEL_GOPASS_PATHS.get(key)
    if gopass_path is None:
        return None
    value = _get_token(gopass_path, _CHANNEL_ENV_KEYS.get(key, []))
    resolved = value or _CHANNEL_DEFAULTS.get(key)
    _channel_cache[key] = resolved
    return resolved


def reset_channel_cache() -> None:
    """Clear the memoized channel-id resolutions (tests / env reload)."""
    _channel_cache.clear()


def _channel_id_required(key: str) -> str:
    """Known channel keys always resolve — defaults guarantee a value."""
    value = _resolve_channel_id(key)
    assert value is not None
    return value


def _resolve_channels() -> dict[str, str]:
    """Resolve the channel map (gopass → env → fallback) lazily on first access.

    Deferred via the package ``__getattr__`` so that importing this package
    never shells out to gopass for the channel ids. Returns the same snapshot
    the eager binding did; memoized into the package namespace on first access.
    """
    return {key: _channel_id_required(key) for key in _CHANNEL_DEFAULTS}
