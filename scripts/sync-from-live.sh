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
  # Phase 1+2 gate: src/ is live + import-rewrite + _vendor + llm_config.
  # Verify the transformed-state invariants (fast, no temp pipeline).
  FAIL=0
  if grep -rl 'app\.domains\.telegram\.rugmunchbot' "$TARGET" --include='*.py' 2>/dev/null | grep -q .; then
    echo "FAIL: stale app.domains.telegram.rugmunchbot refs remain" >&2; FAIL=1
  fi
  for imp in 'app.core.ai_guardrails' 'app.core.shared_utils' 'app.core.channels_registry' 'app.core.redis' 'app.osint_tools' 'app.domains.news_ingest' 'app.domains.telegram.access_guard' 'app.domains.telegram.headless' 'app.llm_config'; do
    if grep -rl "from $imp" "$TARGET" --include='*.py' 2>/dev/null | grep -v '_vendor/' | grep -q .; then
      echo "FAIL: unreplaced import $imp" >&2; FAIL=1
    fi
  done
  for v in ai_guardrails.py shared_utils.py channels_registry.py redis_client.py logging_shim.py logging_config.py degradation.py access_guard.py headless.py headless_client.py headless_models.py osint_tools.py dedup.py news_ingest/models.py; do
    [ -f "$TARGET/_vendor/$v" ] || { echo "FAIL: missing _vendor/$v" >&2; FAIL=1; }
  done
  [ -f "$TARGET/llm_config.py" ] || { echo "FAIL: missing llm_config.py" >&2; FAIL=1; }
  [ -f "$HERE/scripts/rewrite-imports.py" ] && [ -f "$HERE/scripts/vendor-from-backend.sh" ] || { echo "FAIL: missing sync scripts" >&2; FAIL=1; }
  if [ "$FAIL" -eq 0 ]; then
    echo "OK: src/rugmunchbot = live + import rewrite + _vendor + llm_config (invariants hold)."
    exit 0
  fi
  exit 1
fi

echo "Syncing live bot -> src/rugmunchbot ..."
rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' --exclude='_vendor/' --exclude='llm_config.py' "$LIVE/" "$TARGET/"
echo "Sync complete."

echo "Applying import rewrite (app.domains.telegram.rugmunchbot -> rugmunchbot) ..."
python3 "$HERE/scripts/rewrite-imports.py"

echo "Re-vendoring Phase 2a modules (rmi-backend -> _vendor) ..."
bash "$HERE/scripts/vendor-from-backend.sh" >/dev/null

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
