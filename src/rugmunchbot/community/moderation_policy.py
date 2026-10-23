"""Parsing and clamping for a stored per-chat moderation policy.

The stored document is untrusted: it may predate a field, carry a typo, or be
edited by hand. Every value is coerced into its safe range and anything
unreadable falls back to the inert default, so a corrupt document can never
switch enforcement on.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rugmunchbot.community.moderation_models import Policy

__all__ = ["policy_from_mapping", "policy_to_mapping"]

MAX_TRUSTED_HOSTS = 50
MAX_HOST_LEN = 253
HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

#: field -> (inert default, low, high)
INT_FIELDS: dict[str, tuple[int, int, int]] = {
    "strikes_mute": (3, 1, 50),
    "strikes_ban": (5, 1, 100),
    "mute_seconds": (3600, 60, 2_592_000),
    "max_mentions": (8, 0, 100),
    "duplicate_threshold": (3, 1, 50),
    "duplicate_window_s": (120, 10, 3600),
    "new_account_days": (0, 0, 365),
}

#: field -> inert default
BOOL_FIELDS: dict[str, bool] = {
    "dry_run": True,
    "allow_auto_ban": False,
    "delete_on_action": True,
}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
    return default


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_hosts(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    hosts: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        host = item.strip().lower().removeprefix("www.")
        if host and len(host) <= MAX_HOST_LEN and set(host) <= HOST_CHARS:
            hosts.append(host)
        if len(hosts) >= MAX_TRUSTED_HOSTS:
            break
    return tuple(dict.fromkeys(hosts))


def policy_from_mapping(raw: Mapping[str, Any] | None) -> Policy:
    """Build a :class:`Policy` from a stored document, clamped to safe ranges."""
    data = raw if isinstance(raw, Mapping) else {}
    values: dict[str, Any] = {}
    for field, default in BOOL_FIELDS.items():
        values[field] = _as_bool(data.get(field), default)
    for field, (default, low, high) in INT_FIELDS.items():
        values[field] = _as_int(data.get(field), default, low, high)
    values["strikes_ban"] = max(values["strikes_mute"], values["strikes_ban"])
    values["trusted_hosts"] = _as_hosts(data.get("trusted_hosts"))
    return Policy(**values)


def policy_to_mapping(policy: Policy) -> dict[str, Any]:
    """Serialise *policy* into the stable shape :func:`policy_from_mapping` reads."""
    return {
        "dry_run": policy.dry_run,
        "allow_auto_ban": policy.allow_auto_ban,
        "strikes_mute": policy.strikes_mute,
        "strikes_ban": policy.strikes_ban,
        "mute_seconds": policy.mute_seconds,
        "max_mentions": policy.max_mentions,
        "duplicate_threshold": policy.duplicate_threshold,
        "duplicate_window_s": policy.duplicate_window_s,
        "new_account_days": policy.new_account_days,
        "trusted_hosts": list(policy.trusted_hosts),
        "delete_on_action": policy.delete_on_action,
    }
