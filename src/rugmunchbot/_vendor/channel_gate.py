# VENDORED 2026-10-23 from rmi-backend/app/core/channel_gate.py
# (canonical: rmi-content-pipeline/pipeline/channel_gate.py — do not edit here;
#  re-vendor: bash scripts/refresh-vendors.sh)
# ---
"""Per-channel live gate. Every channel is DRY until it is named explicitly.

The only switch that can make a channel post is its own variable:

    RMI_CHANNEL_DRY_RUN_<ROLE>=0      e.g. RMI_CHANNEL_DRY_RUN_NEWS=0

Design rules (deliberate, not incidental):
  * unset            -> dry (safe default, no migration needed)
  * any value != "0" -> dry (a typo can only ever keep a channel quiet)
  * ``RMI_CHANNEL_DRY_RUN=1`` forces *every* channel dry, overriding per-role
  * ``RMI_CHANNEL_DRY_RUN=0`` is IGNORED — a single global flip cannot put the
    whole fleet live; that is the incident this gate exists to prevent
  * ``assert_single_live`` fails loudly when more than one role is live, so
    "one channel at a time" is checkable rather than aspirational
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

LIVE_VALUE = "0"
FORCE_DRY_VALUES = frozenset({"1", "true", "yes", "on"})


def _env_key(role: str) -> str:
    """Per-channel variable name for a role (upper snake)."""
    return f"RMI_CHANNEL_DRY_RUN_{role.strip().upper().replace('-', '_')}"


def is_live(role: str, env: Mapping[str, str] | None = None) -> bool:
    """True only when this ONE role is explicitly enabled and not force-dried."""
    source = env if env is not None else os.environ
    global_value = source.get("RMI_CHANNEL_DRY_RUN", "").strip().lower()
    if global_value in FORCE_DRY_VALUES:
        return False
    return source.get(_env_key(role), "").strip() == LIVE_VALUE


def live_roles(roles: tuple[str, ...], env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Every role currently live, in registry order."""
    return tuple(role for role in roles if is_live(role, env))


def gate_line(role: str, env: Mapping[str, str] | None = None) -> str:
    """One log line describing the decision for a role, with its variable."""
    state = "LIVE" if is_live(role, env) else "DRY"
    return f"{role}:{state} [{_env_key(role)}]"


def assert_single_live(roles: tuple[str, ...], env: Mapping[str, str] | None = None) -> str | None:
    """Return a violation message when more than one role is live, else None.

    Live enablement is a sequence, not a batch: one channel, one sweep, one
    observed result, then the next. More than one live role is a config bug.
    """
    live = live_roles(roles, env)
    if len(live) > 1:
        return f"multiple channels live at once: {', '.join(live)}"
    return None
