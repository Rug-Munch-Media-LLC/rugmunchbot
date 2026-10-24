"""Group messages into entity/day alerts and gate them (pure).

One entity per bucket per UTC day. Every channel that pushed it becomes a
source on the same alert — the caller posts one card, not N.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.shill_alerts.dedupe import today
from rugmunchbot.channels.shill_alerts.gate import DROP, decide
from rugmunchbot.channels.shill_alerts.models import Alert
from rugmunchbot.channels.shill_alerts.signals import Signal, extract_signals

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

_EVM = re.compile(r"0x[0-9a-fA-F]{40}")


def _chain_of(entity: str, message: Mapping[str, Any]) -> str:
    explicit = str(message.get("chain") or "")
    if explicit:
        return explicit
    return "ethereum" if _EVM.fullmatch(entity or "") else "solana"


def _link(message: Mapping[str, Any]) -> str:
    raw = str(message.get("link") or message.get("url") or message.get("source_url") or "")
    channel = str(message.get("channel") or message.get("source_channel") or "").lstrip("@")
    mid = str(message.get("message_id") or message.get("id") or "")
    if raw.startswith("https://t.me/"):
        return raw
    return f"https://t.me/{channel}/{mid}" if channel and mid else raw


def _merge(existing: dict[str, Signal], incoming: Iterable[Signal]) -> None:
    """Keep the strongest instance of each signal name; join details."""
    for sig in incoming:
        prev = existing.get(sig.name)
        if prev is None or sig.weight > prev.weight:
            existing[sig.name] = sig


def plan_alerts(
    messages: Iterable[Mapping[str, Any]],
    *,
    evidence_for: Callable[[str], Mapping[str, Any]] | None = None,
    day: str | None = None,
    min_confidence: float = 0.5,
) -> list[Alert]:
    """Return gated alerts (``main`` and ``review``; ``drop`` is discarded).

    ``evidence_for(entity)`` supplies external facts per entity (token age,
    on-chain risk, known-bad actors, cadence). It is optional: without it the
    lane still detects text-only promo, just with fewer families.
    """
    bucket_day = day or today()
    groups: dict[str, dict[str, Any]] = {}

    for message in messages or []:
        sig = extract_signals(dict(message), evidence={}, repeat_channels=0)
        entity = sig.entity
        if not entity:
            continue
        group = groups.setdefault(
            entity,
            {"messages": [], "channels": {}, "chains": {}},
        )
        group["messages"].append(dict(message))
        channel = str(message.get("channel") or message.get("source_channel") or "unknown")
        group["channels"].setdefault(channel, _link(message))
        group["chains"].setdefault(_chain_of(entity, message), None)

    alerts: list[Alert] = []
    for entity, group in groups.items():
        channel_count = len(group["channels"])
        evidence = dict(evidence_for(entity) or {}) if evidence_for else {}
        merged: dict[str, Signal] = {}
        for message in group["messages"]:
            sig = extract_signals(
                message,
                evidence=evidence,
                repeat_channels=channel_count,
            )
            _merge(merged, sig.signals)
        signals = tuple(sorted(merged.values(), key=lambda s: (-s.weight, s.name)))
        verdict = decide(signals, min_confidence=min_confidence)
        if verdict.route == DROP:
            continue
        chain = next(iter(group["chains"]), "unknown")
        alerts.append(
            Alert(
                entity=entity,
                chain=chain,
                day=bucket_day,
                verdict=verdict,
                signals=signals,
                sources=tuple(group["channels"].values())[:20],
                channels=tuple(group["channels"])[:20],
                risk=dict(evidence.get("onchain_risk") or {}),
            )
        )
    return sorted(alerts, key=lambda a: (-a.verdict.confidence, a.entity))
