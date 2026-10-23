"""Price-alert rules — argument parsing, per-user quota, duplicate detection.

Pure functions (no DB, no network): ``_commands.py`` and ``_delete.py`` call
them so the alert contract lives in ONE place. A price is never invented here:
``ABOVE``/``BELOW`` require an explicit positive target, ``CHANGE_5``/``ANY``
carry ``None`` and let the checker apply its percentage rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugmunchbot.formatters import detect_chain

DIRECTIONS = ("above", "below", "change_5", "any")
PRICE_REQUIRED = ("above", "below")
# Per-user caps on ACTIVE price alerts (separate from the watchlist cap).
ALERT_LIMITS = {"free": 0, "scout": 10, "hunter": 25, "pro": 100}
USAGE = "/alert ABOVE|BELOW|CHANGE_5|ANY <address> [price]"


@dataclass(frozen=True)
class AlertSpec:
    """One validated alert rule."""

    direction: str
    address: str
    chain: str
    price: float | None


def parse_alert_args(args: list[str]) -> tuple[AlertSpec | None, str | None]:
    """Parse ``/alert <DIRECTION> <address> [price]`` → ``(spec, error)``."""
    if len(args) < 2:
        return None, f"Usage: {USAGE}"
    direction = args[0].strip().lower()
    if direction not in DIRECTIONS:
        return None, f"Unknown direction — choose one of: {', '.join(DIRECTIONS)}."
    address = args[1].strip()
    chain = detect_chain(address)
    if chain == "unknown":
        return None, "Chain not detected — use an EVM (0x...) or Solana address."
    price: float | None = None
    if len(args) > 2:
        try:
            price = float(args[2])
        except (TypeError, ValueError):
            return None, "Target price must be a number (e.g. 0.005)."
        if price <= 0:
            return None, "Target price must be greater than zero."
    if direction in PRICE_REQUIRED and price is None:
        return None, "ABOVE/BELOW alerts need a target price."
    return AlertSpec(direction, address, chain, price), None


def active_alert_count(entries: list[dict[str, Any]]) -> int:
    """Count entries that carry an ACTIVE alert rule."""
    return sum(1 for w in entries if w.get("alert_price") and w.get("is_active"))


def quota_error(tier: str, active: int) -> str | None:
    """Return the refusal message when the user is at their alert cap."""
    limit = ALERT_LIMITS.get(tier, 0)
    if limit == 0:
        return "Price alerts require Scout tier or higher."
    if active >= limit:
        return (
            f"Alert limit reached ({limit} on your tier). Remove one with /alert delete <address>."
        )
    return None


def is_duplicate(entry: dict[str, Any], spec: AlertSpec) -> bool:
    """Same token + direction + target ⇒ update in place instead of a new row."""
    if str(entry.get("token_address", "")).lower() != spec.address.lower():
        return False
    if str(entry.get("alert_direction") or "").lower() != spec.direction:
        return False
    existing = entry.get("alert_price")
    if existing is None or spec.price is None:
        return existing is None and spec.price is None
    return abs(float(existing) - spec.price) < 1e-12
