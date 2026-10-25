"""scheduler_state — startup hydrate / end-of-tick flush wrappers."""

from __future__ import annotations

from ._singleton import _state


async def load_state(seed: dict[str, set[str] | dict[str, None]]) -> bool:
    """Hydrate the scheduler's dedupe sets + last-run from Redis (startup)."""
    return await _state.load(seed)


async def save_state(
    snapshot: dict[str, set[str] | dict[str, None]], job: str | None = None
) -> bool:
    """Persist the posted-key snapshot + last-run hash (end of each tick)."""
    if job is not None:
        _state.mark_last_run(job)
    return await _state.save(snapshot)
