"""Per-chat trusted-user allowlist.

Trust is an explicit, auditable grant — never inferred from rank. It buys ONE
thing: the account skips the *entrance* quarantine (it still has to solve the
captcha unless an operator marked it verified, and it remains fully subject to
scam-link, promo, flood and ban rules). There is deliberately no admin bypass:
policy.exempt_admins only exempts configured chat admins from message rules and
defaults to off, so a compromised admin account cannot quietly shield a spam
ring.

Seeds: RUGMUNCH_MOD_TRUSTED_USER_IDS (global) plus per-chat grants held in
memory for the process lifetime (a restart forgets them — fail-safe: trust is
re-earned, never silently inherited).
"""

from __future__ import annotations

import os

from rugmunchbot.moderation.guard_settings import for_chat

_grants: dict[int, set[int]] = {}


def _env_ids() -> set[int]:
    raw = os.getenv("RUGMUNCH_MOD_TRUSTED_USER_IDS", "")
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            out.add(int(chunk))
    return out


def is_trusted(chat_id: int, user_id: int) -> bool:
    """True when user_id holds an env or per-chat trust grant."""
    if user_id in _grants.get(chat_id, set()):
        return True
    return user_id in _env_ids() or user_id in for_chat(chat_id).trusted_user_ids


def trust(chat_id: int, user_id: int) -> None:
    _grants.setdefault(chat_id, set()).add(int(user_id))


def untrust(chat_id: int, user_id: int) -> None:
    _grants.get(chat_id, set()).discard(int(user_id))


def snapshot(chat_id: int) -> tuple[int, ...]:
    """Every currently trusted id for the chat, sorted (admin view)."""
    ids = _grants.get(chat_id, set()) | _env_ids()
    ids |= set(for_chat(chat_id).trusted_user_ids)
    return tuple(sorted(ids))


def reset() -> None:
    """Drop in-process grants (tests, and a deliberate fleet-wide reset)."""
    _grants.clear()


__all__ = ["is_trusted", "reset", "snapshot", "trust", "untrust"]
