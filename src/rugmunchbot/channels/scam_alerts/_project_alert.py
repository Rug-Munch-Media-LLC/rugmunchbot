"""Build a project-level scam alert from sourced evidence.

Contract (operator 2026-10-24): a project alert states WHY the project is a
scam (evidence), WHO is pushing it (channel / wallet / deployer), the SOURCE,
a TIMESTAMP, a CONFIDENCE derived from the evidence, and a DISCLAIMER.
No evidence → no alert. Confidence is computed from the evidence weights —
never invented, never inflated by a single social claim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ._project_evidence import normalise_evidence, normalise_pushers

SCHEMA_VERSION = 2

#: Severity bands keyed on the strongest single piece of evidence.
_SEVERITY_FLOORS = ((40, "critical"), (35, "high"), (20, "medium"))

DISCLAIMER = (
    "Evidence-based warning, not a legal finding. Every claim above cites its "
    "source; we do not accuse identifiable people without published evidence. "
    "Verify before trading — you can lose everything."
)


def _identity(project: dict[str, Any]) -> tuple[str, str, list[dict[str, str]]]:
    """(name, symbol, addresses) with EVM addresses lower-cased."""
    name = " ".join(str(project.get("name") or "").split())[:80]
    symbol = str(project.get("symbol") or "").strip().lstrip("$")[:16]
    addresses: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in project.get("addresses") or []:
        if isinstance(raw, str):
            address, chain = raw.strip(), str(project.get("chain") or "")
        elif isinstance(raw, dict):
            address = str(raw.get("address") or "").strip()
            chain = str(raw.get("chain") or project.get("chain") or "")
        else:
            continue
        if not address:
            continue
        if address.startswith("0x"):
            address = address.lower()
        if address in seen:
            continue
        seen.add(address)
        addresses.append({"address": address, "chain": chain.lower()})
    return name, symbol, addresses


def _confidence(evidence: list[dict[str, Any]]) -> tuple[float, str, str]:
    """(score, label, basis) from evidence weights — deterministic, documented.

    ``score = (strongest + 5 * corroborating_kinds) / 50`` capped at 0.95, so a
    lone social claim (20) lands at 0.40 "low" while protocol-level proof (40)
    lands at 0.80 "high" and one corroborating kind reaches 0.90.
    """
    strongest = max(item["weight"] for item in evidence)
    corroborating = len({item["kind"] for item in evidence}) - 1
    score = round(min(0.95, (strongest + 5 * corroborating) / 50), 2)
    label = "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"
    basis = f"{len(evidence)} sourced evidence item(s), strongest proof weight {strongest}/40"
    return score, label, basis


def _severity(evidence: list[dict[str, Any]]) -> str:
    strongest = max(item["weight"] for item in evidence)
    for floor, label in _SEVERITY_FLOORS:
        if strongest >= floor:
            return label
    return "low"


def build_project_alert(
    project: dict[str, Any],
    evidence: list[dict[str, Any]] | None,
    pushers: list[dict[str, Any]] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return a postable project alert, or ``None`` when it is not earned.

    ``None`` means: no identity, no recognised+sourced evidence, or nothing
    left after normalisation. Callers must treat ``None`` as "do not post".
    """
    name, symbol, addresses = _identity(project or {})
    if not (name or symbol or addresses):
        return None
    kept = normalise_evidence(evidence)
    if not kept:
        return None

    stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
    confidence, label, basis = _confidence(kept)
    sources: list[str] = []
    for item in kept:
        if item["source"] not in sources:
            sources.append(item["source"])
    for pusher in normalise_pushers(pushers):
        if pusher["source"] not in sources:
            sources.append(pusher["source"])

    return {
        "schema": SCHEMA_VERSION,
        "project": {
            "name": name,
            "symbol": symbol,
            "chain": str((project or {}).get("chain") or "").lower(),
            "addresses": addresses,
        },
        "severity": _severity(kept),
        "why": kept,
        "pushers": normalise_pushers(pushers),
        "sources": sources,
        "confidence": {"score": confidence, "label": label, "basis": basis},
        "timestamp": stamp,
        "first_seen": str((project or {}).get("first_seen") or "") or None,
        "disclaimer": DISCLAIMER,
    }
