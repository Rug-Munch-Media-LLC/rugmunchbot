"""Production seam for the watch-alert gate: tier, cap, one admission answer.

``watch_alerts.AlertGate`` owns the policy — dedupe, quiet hours, a sliding
hourly cap. This module owns the two things a worker cannot derive by itself:
the tier of a chat, and one gate per tier so the cap matches what the user paid
for. ``alert_worker`` asks ``admit`` and nothing else, so the worker never
holds premium state and a second caller cannot invent a different cap.

The tier lookup is a module seam (``configure``), not a patch target: tests
install a pure lookup and a fixed clock, production leaves both unset and the
tier comes from the database. A lookup failure floors to ``free`` — a database
hiccup must not lift a cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.premium.gating import allows
from rugmunchbot.premium.watch_alerts import HOURLY_CAP, AlertGate
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

#: The one queue type this gate owns; every other alert type passes untouched.
WATCH_TYPE = "token.monitor_change"

logger = get_logger(__name__)

_GATES: dict[str, AlertGate] = {}
_READ_TIER: Callable[[int], str] | None = None
_GATE_KWARGS: dict[str, Any] = {}


def configure(
    read_tier: Callable[[int], str] | None = None,
    *,
    quiet: Any = None,
    clock: Callable[[], float] | None = None,
) -> None:
    """Install seams for tests (``None`` restores production defaults)."""
    global _READ_TIER
    _READ_TIER = read_tier
    _GATES.clear()
    _GATE_KWARGS.clear()
    if quiet is not None:
        _GATE_KWARGS["quiet"] = quiet
    if clock is not None:
        _GATE_KWARGS["clock"] = clock


def tier_for(user_id: int) -> str:
    """Tier of one chat, floored to ``free`` when it cannot be established."""
    try:
        raw = (_READ_TIER or _db_tier)(user_id)
    except (LookupError, OSError, RuntimeError, TypeError, ValueError):
        logger.warning("watch_gate: tier lookup failed user=%s", user_id, exc_info=True)
        return "free"
    name = str(raw or "").strip().lower()
    return name if allows(name, "watch_alerts") else "free"


def _db_tier(user_id: int) -> str:
    """Tier from the store — imported lazily so this module stays I/O-free."""
    from rugmunchbot import db

    return db.get_user_tier(user_id)


def gate_for(tier: str) -> AlertGate:
    """The single gate for a tier — the cap follows the tier, not the caller."""
    paid = allows(tier, "watch_alerts")
    cap = HOURLY_CAP["paid"] if paid else HOURLY_CAP["free"]
    return _GATES.setdefault(tier, AlertGate(cap=cap, **_GATE_KWARGS))


def admit(item: dict[str, Any]) -> str:
    """``send`` / ``hold`` / ``drop`` for one queued watch alert.

    An item with no chat or no event id is ``send``: the gate exists to stop
    repeats, never to invent a reason to withhold a delivery.
    """
    user_id = item.get("user_id")
    key = str(item.get("event_id") or "")
    if not isinstance(user_id, int) or user_id <= 0 or not key:
        return "send"
    return gate_for(tier_for(user_id)).admit(user_id, key)


def screen(item: dict[str, Any], forget: Callable[[str], None] | None = None) -> str:
    """Verdict for one queued item: ``send`` (not a watch, or admitted), ``hold``, ``drop``.

    ``forget`` un-records the event id when the answer is a hold: the worker
    records ids *before* the gate runs, and a deferred alert must still be
    deliverable on its retry.
    """
    if str(item.get("type") or "") != WATCH_TYPE:
        return "send"
    verdict = admit(item)
    if verdict == "hold" and forget is not None:
        forget(str(item.get("event_id") or ""))
    return verdict
