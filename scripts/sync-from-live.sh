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

# Verify
if diff -rq --exclude='__pycache__' --exclude='*.pyc' "$LIVE" "$TARGET" >/dev/null; then
  echo "OK: src/rugmunchbot matches the live bot exactly."
else
  echo "WARNING: drift remains after sync — investigate." >&2
  diff -rq --exclude='__pycache__' --exclude='*.pyc' "$LIVE" "$TARGET" | head -20
  exit 1
fi

echo
echo "Next: git add src/rugmunchbot && git commit -m 'chore(bot): sync from live (phase 0 snapshot)'"
