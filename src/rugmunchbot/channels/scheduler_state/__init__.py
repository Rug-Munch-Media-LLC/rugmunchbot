"""Durable scheduler state for the JobQueue channel jobs (Redis-backed).

Facade package (split from ``channels/scheduler_state.py``, 2026-10-25): the
complete pre-split public surface stays importable from this module.

* **Posted-key dedupe** — ``bot:channels:posted:<channel>`` (TTL 72h) plus the
  global set ``bot:channels:posted:__global__``; hydrated at startup, flushed
  every tick, appended per new key via ``spawn_persist``.
* **Last-run timestamps** — ``bot:channels:last_run`` (Redis HASH, TTL 30d).
* **Sync durable check** — ``has_posted`` (short-timeout client, hot path).

Fail-open by contract: every Redis call is best-effort and never raises;
Redis down degrades to the in-memory behaviour and self-heals on re-probe.
The scheduler passes its own ``_posted_keys`` dict in — one source of truth.
"""

from __future__ import annotations

from ._persist import load_state, save_state
from ._singleton import (
    get_state,
    is_available,
    last_run_iso,
    mark_last_run,
    reset_state,
    spawn_persist,
)
from ._state import SchedulerState as SchedulerState
from ._sync import has_posted
from ._sync import reset_sync as reset_sync

__all__ = [
    "get_state",
    "has_posted",
    "is_available",
    "last_run_iso",
    "load_state",
    "mark_last_run",
    "reset_state",
    "save_state",
    "spawn_persist",
]
