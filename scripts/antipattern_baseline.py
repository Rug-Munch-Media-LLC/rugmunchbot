#!/usr/bin/env python3
"""antipattern_baseline.py — write an audited, MEASURED antipattern baseline.

Backs `check-antipatterns.py --rebaseline`. A rebaseline is a measurement of the
tree, never a policy raise: the counts are written exactly as measured (no
headroom) together with the reason, the date and the commit measured at. The
ratchet is shrink-only again immediately afterwards, so the only way to lower the
numbers is to fix the code (the debt lane).
"""

from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def write_rebaseline(path: Path, counts: dict[str, int], reason: str, root: Path) -> int:
    """Record a fresh measured baseline with its audit trail (rc 0, or 2 = no reason)."""
    if not reason.strip():
        print("--rebaseline requires --reason (a re-measurement is never silent)", file=sys.stderr)
        return 2
    # fixed argv, no shell; the binary is resolved, not taken from PATH blindly
    head = subprocess.run(  # noqa: S603
        [shutil.which("git") or "git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    payload = {
        "measured_at": datetime.date.today().isoformat(),
        "measured_commit": head or "unknown",
        "reason": reason,
        "rules": counts,
        "total": sum(counts.values()),
    }
    path.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"baseline re-measured: {path} (total {payload['total']})")
    print(f"  at {payload['measured_commit']} on {payload['measured_at']}: {reason}")
    return 0
