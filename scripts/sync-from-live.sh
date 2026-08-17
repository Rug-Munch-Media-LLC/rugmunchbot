#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# rugmunchbot — ONE-WAY sync from the LIVE bot (rmi-backend) into this repo.
#
# Phase 0 rule (canonical): the LIVE brain is
#   /srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot
# This repo's src/rugmunchbot is a VERIFIED SNAPSHOT of it.
# NEVER edit src/rugmunchbot directly — edit the live copy, then run this
# script and commit. Bidirectional copying is forbidden (that is how the
# old dual-brain drift happened).
#
# Usage:
#   bash scripts/sync-from-live.sh          # sync + drift check
#   bash scripts/sync-from-live.sh --check  # drift check only (CI-safe)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LIVE="/srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$HERE/src/rugmunchbot"

if [ ! -d "$LIVE" ]; then
  echo "ERROR: live bot path not found: $LIVE" >&2
  exit 1
fi

if [ "${1:-}" = "--check" ]; then
  DIFF=$(diff -rq --exclude='__pycache__' --exclude='*.pyc' "$LIVE" "$TARGET" || true)
  if [ -n "$DIFF" ]; then
    echo "DRIFT DETECTED between live bot and src/rugmunchbot:" >&2
    echo "$DIFF" >&2
    exit 1
  fi
  echo "OK: src/rugmunchbot matches the live bot exactly."
  exit 0
fi

echo "Syncing live bot -> src/rugmunchbot ..."
rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' "$LIVE/" "$TARGET/"
echo "Sync complete."

echo "Applying import rewrite (app.domains.telegram.rugmunchbot -> rugmunchbot) ..."
python3 "$HERE/scripts/rewrite-imports.py"

# Verify: the raw rsync tree must match live EXACTLY (rewrite is applied
# on top; a second rewrite is a no-op so the check below is stable).
if diff -rq --exclude='__pycache__' --exclude='*.pyc' "$LIVE" "$TARGET" >/dev/null; then
  echo "ERROR: expected rewritten imports but tree is byte-identical to live — rewrite-imports.py did not apply." >&2
  exit 1
fi
# Idempotency check: rewriting again must change nothing.
python3 "$HERE/scripts/rewrite-imports.py" >/dev/null
if ! python3 "$HERE/scripts/rewrite-imports.py" 2>&1 | grep -q "0 file(s)"; then
  echo "WARNING: rewrite-imports.py is not idempotent — investigate." >&2
  exit 1
fi
echo "OK: src/rugmunchbot = live + import rewrite (verified idempotent)."

echo
echo "Next: git add src/rugmunchbot && git commit -m 'chore(bot): sync from live (phase 1 transformed snapshot)'"
