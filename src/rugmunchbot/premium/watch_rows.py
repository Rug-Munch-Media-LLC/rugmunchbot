"""Turn watch events into rows for the canonical ``alerts`` table.

The queue is the one that already exists: the backend's
``app.domains.alerts.postgres_store`` inserts the rows and owns dedupe. This module only decides content, so a watch
alert is indistinguishable in shape from a detector alert and needs no second
consumer.

``watchers_n`` travels in ``evidence``: how many chats asked to be told is
part of the signal, and it costs no schema change.
"""

from __future__ import annotations

import hashlib
from typing import Any


def alert_id_for(*parts: str) -> str:
    """Deterministic 16-hex identity for an event.

    Ported verbatim from ``app.domains.alerts.postgres_store.alert_id_for``
    (rmi-backend): pure hash, no store dependency, so the row identity a watch
    alert gets is the same one the backend's ``ON CONFLICT DO NOTHING`` uses.
    """
    raw = ":".join(str(p).lower() for p in parts)
    return "alert_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


_SEVERITY = {
    "cashed_out": "warning",
    "lp_unlocked": "critical",
    "new_bundle_wallet": "warning",
    "authority_changed": "critical",
}

_LEAD = {
    "cashed_out": "Top holder cashed out",
    "lp_unlocked": "Liquidity lock gone",
    "new_bundle_wallet": "New bundled wallet",
    "authority_changed": "Token authority changed",
}


def _short(value: str | None) -> str:
    if not value:
        return "none"
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else value


def describe_event(event: dict[str, Any]) -> str:
    """One postable sentence: who, from what, to what."""
    kind = event["kind"]
    if kind == "cashed_out":
        now = "gone" if event.get("after_pct") is None else f"{event['after_pct']:.2f}%"
        return f"{_short(event.get('wallet'))} held {event['before_pct']:.2f}%, now {now}"
    if kind == "lp_unlocked":
        locked = event.get("after_pct")
        usd = event.get("after_usd")
        parts = ["lock no longer reported" if locked == 0.0 else f"locked {locked}%"]
        if usd is not None:
            parts.append(f"liquidity ${usd:,.0f}")
        return ", ".join(parts)
    if kind == "new_bundle_wallet":
        return f"{_short(event.get('wallet'))} joined the bundled set this cycle"
    field = event.get("field", "authority")
    return f"{field}: {_short(event.get('before_value'))} → {_short(event.get('after_value'))}"


