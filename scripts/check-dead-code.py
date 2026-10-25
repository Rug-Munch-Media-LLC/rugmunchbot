#!/usr/bin/env python3
"""Dead-code ratchet: fails when vulture finds more dead symbols than the baseline."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "dead-code-baseline.json"
SUPPRESSIONS = ROOT / "scripts" / "dead-code-suppressions.json"
EXCLUDE = ".git,*_test.py,__pycache__,tests,eval,scripts/archive,src/rugmunchbot/_vendor"
HIT = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+): unused (?P<kind>function|method|class) '(?P<name>[^']+)'")


def main() -> int:
    baseline = json.loads(BASELINE.read_text())
    suppressions = json.loads(SUPPRESSIONS.read_text())["suppressions"]
    proc = subprocess.run(
        [sys.executable, "-m", "vulture", "--min-confidence", "60", "--exclude", EXCLUDE, "src"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1, 3):
        print("DEAD-CODE: vulture failed rc=%s: %s" % (proc.returncode, proc.stderr.strip()[:300]))
        return 2
    found, suppressed, used = [], 0, set()
    for line in proc.stdout.splitlines():
        match = HIT.match(line.strip())
        if not match:
            continue
        path, name = match.group("path"), match.group("name")
        entry = next((s for s in suppressions
                      if path.startswith(s.get("path", "\0"))
                      and s.get("symbol") in (None, name)), None)
        if entry is None:
            found.append("%s:%s %s" % (path, match.group("line"), name))
        else:
            suppressed += 1
            used.add("%s::%s" % (entry["path"], entry.get("symbol")))
    limit = baseline["dead_symbols"]
    print("dead symbols: %d (baseline %d, suppressed %d)" % (len(found), limit, suppressed))
    for item in found[:25]:
        print("   ", item)
    for entry in suppressions:
        if "%s::%s" % (entry["path"], entry.get("symbol")) not in used:
            print("STALE suppression (matches nothing, delete it): %s::%s"
                  % (entry["path"], entry.get("symbol")))
    if len(found) > limit:
        print("FAIL: dead code grew by %d. Delete it or add a reasoned suppression." % (len(found) - limit))
        return 1
    if len(found) < limit:
        print("OK, tighter than baseline by %d: set dead-code-baseline.json to %d."
              % (limit - len(found), len(found)))
    print("OK: dead-code ratchet holds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
