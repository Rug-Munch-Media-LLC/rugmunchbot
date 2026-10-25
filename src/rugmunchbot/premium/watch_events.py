"""What changed between two snapshots — four event kinds, nothing else.

The four kinds are the ones a holder actually acts on:

``cashed_out``
    A top holder that was there last cycle is gone (or is down to a fifth of
    its previous share). Someone with size left the book.
``lp_unlocked``
    The lock that was reported last cycle is no longer reported, or liquidity
    halved. The floor under the price moved.
``new_bundle_wallet``
    A wallet that bundles supply appeared that was not in the previous set.
    Coordinated accumulation is happening *now*.
``authority_changed``
    Mint/freeze/owner changed, including revocation. Whoever holds the lever
    changed hands.

Only transitions produce events. Two states that are equal — including two
states that are equally unknown (``None == None``) — produce nothing, so a
quiet token stays quiet and alert fatigue never sets in.
"""

from __future__ import annotations

from typing import Any

#: A holder at or above this share is worth tracking; dust is not.
MIN_HOLDER_PCT = 0.5
#: Remaining share, relative to the previous share, that counts as a cash-out.
CASHD_OUT_RATIO = 0.2
#: Liquidity loss, relative to the previous cycle, that means the LP moved.
LP_DROP_RATIO = 0.5


def _holder_pcts(snapshot: dict[str, Any]) -> dict[str, Any]:
    """wallet -> reported share for the top holders of one snapshot."""
    top = (snapshot.get("holders") or {}).get("top") or []
    return {h["wallet"]: h.get("pct") for h in top}


def _cash_out_event(wallet: str, pct: Any, now: Any) -> dict[str, Any] | None:
    """One ``cashed_out`` event, or ``None`` when the holder is still there."""
    if pct is None or pct < MIN_HOLDER_PCT:
        return None
    if now is not None and now > pct * CASHD_OUT_RATIO:
        return None
    return {
        "kind": "cashed_out",
        "wallet": wallet,
        "before_pct": pct,
        "after_pct": now,
        "detail": "holder gone from top holders" if now is None else "share collapsed",
    }


def _cashed_out(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    before = _holder_pcts(previous)
    after = _holder_pcts(current)
    events = [_cash_out_event(wallet, pct, after.get(wallet)) for wallet, pct in before.items()]
    return [event for event in events if event is not None]


def _lp_unlocked(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    before = previous.get("lp") or {}
    after = current.get("lp") or {}
    locked_before = before.get("locked_pct")
    locked_after = after.get("locked_pct")
    unlocked = locked_before not in (None, 0.0) and locked_after == 0.0
    usd_before = before.get("liquidity_usd")
    usd_after = after.get("liquidity_usd")
    drained = usd_before and usd_after is not None and usd_after <= usd_before * LP_DROP_RATIO
    if not (unlocked or drained):
        return []
    return [
        {
            "kind": "lp_unlocked",
            "wallet": after.get("owner"),
            "before_pct": locked_before,
            "after_pct": locked_after,
            "before_usd": usd_before,
            "after_usd": usd_after,
            "detail": "lock removed" if unlocked else "liquidity halved or worse",
        }
    ]


def _new_bundle_wallet(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    before = set(previous.get("bundle_wallets") or [])
    return [
        {
            "kind": "new_bundle_wallet",
            "wallet": wallet,
            "before_pct": None,
            "after_pct": None,
            "detail": "not bundled last cycle",
        }
        for wallet in sorted(set(current.get("bundle_wallets") or []) - before)
    ]


def _authority_changed(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    before = previous.get("authority") or {}
    after = current.get("authority") or {}
    events: list[dict[str, Any]] = []
    for field in ("mint", "freeze", "owner"):
        was, now = before.get(field), after.get(field)
        if was == now:
            continue
        if was is None:
            continue  # nothing was known before: not a change, just new information
        events.append(
            {
                "kind": "authority_changed",
                "wallet": now or was,
                "field": field,
                "before_pct": None,
                "after_pct": None,
                "before_value": was,
                "after_value": now,
                "detail": f"{field} authority revoked"
                if now is None
                else f"{field} authority moved",
            }
        )
    return events


def diff_snapshots(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> list[dict[str, Any]]:
    """Return the events that happened between ``previous`` and ``current``.

    Args:
        previous: the snapshot stored on the last cycle, or ``None`` on the
            first cycle (which is a baseline, not an event).
        current: the snapshot just taken.

    Returns:
        One dict per transition, each with ``kind`` plus the before/after
        values that justify it. Empty when nothing changed.
    """
    if not previous:
        return []
    return [
        *_cashed_out(previous, current),
        *_lp_unlocked(previous, current),
        *_new_bundle_wallet(previous, current),
        *_authority_changed(previous, current),
    ]
