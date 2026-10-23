"""Fingerprint + ledger dedupe for project-level scam alerts.

Identity is ``project + evidence``: the same project re-flagged with NEW
evidence is a new alert (worth posting); the same project with the same
evidence is a duplicate (never posted twice). Order-independent.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

LIVE_LEDGER = "/var/lib/rugmunchbot/scam_project_alerted.jsonl"
DRYRUN_LEDGER = "/var/lib/rugmunchbot/scam_project_alerted.dryrun.jsonl"


def ledger_path(dry_run: bool) -> Path:
    """Ledger for this mode — dry runs never poison the live dedupe.

    Env is read per call (not at import) so tests and operators can retarget
    the ledger without reloading the module.
    """
    if dry_run:
        return Path(os.getenv("RMI_SCAM_PROJECT_DRYRUN_LEDGER", DRYRUN_LEDGER))
    return Path(os.getenv("RMI_SCAM_PROJECT_LEDGER", LIVE_LEDGER))


def alert_fingerprint(alert: dict[str, Any]) -> str:
    """sha256 over project identity + sorted evidence identity."""
    project = alert.get("project") or {}
    addresses = sorted(a.get("address", "") for a in project.get("addresses") or [])
    evidence = sorted(
        f"{item.get('kind')}|{str(item.get('detail', '')).lower()}|"
        f"{str(item.get('source', '')).lower()}"
        for item in alert.get("why") or []
    )
    payload = json.dumps(
        {
            "name": str(project.get("name") or "").lower(),
            "symbol": str(project.get("symbol") or "").lower(),
            "addresses": addresses,
            "evidence": evidence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def is_duplicate(fingerprint: str, path: Path) -> bool:
    """True when this fingerprint already posted (missing ledger = not seen)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            if json.loads(line).get("fp") == fingerprint:
                return True
        except json.JSONDecodeError:
            continue
    return False


def record(fingerprint: str, alert: dict[str, Any], path: Path) -> None:
    """Append one fingerprint to the ledger (best effort — never raises)."""
    project = alert.get("project") or {}
    row = {
        "fp": fingerprint,
        "name": project.get("name"),
        "symbol": project.get("symbol"),
        "severity": alert.get("severity"),
        "confidence": (alert.get("confidence") or {}).get("score"),
        "timestamp": alert.get("timestamp"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        return
