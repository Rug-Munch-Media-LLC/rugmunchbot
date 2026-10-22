#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# rugmunchbot — refresh vendored modules from rmi-backend + verify invariants.
#
# Phase 3 (single brain): src/rugmunchbot IS the canonical bot code — there
# is no "live copy" to rsync from anymore. The only one-way pulls left are
# the VENDORED modules (frozen copies of rmi-backend platform code) and the
# two documented bot-file fixes (web.py). Run this after backend changes to
# any vendored module, then commit.
#
# Usage:
#   bash scripts/refresh-vendors.sh          # refresh + verify
#   bash scripts/refresh-vendors.sh --check  # invariants only (CI-safe)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$HERE/src/rugmunchbot"

if [ "${1:-}" = "--check" ]; then
  FAIL=0
  if rg -l --no-heading --color never -e 'app\.domains\.telegram\.rugmunchbot' -g '*.py' "$TARGET" 2>/dev/null | grep -q .; then
    echo "FAIL: stale app.domains.telegram.rugmunchbot refs remain" >&2
    FAIL=1
  fi
  for imp in 'app.core.ai_guardrails' 'app.core.shared_utils' 'app.core.channels_registry' 'app.core.redis' 'app.osint_tools' 'app.domains.news_ingest' 'app.domains.telegram.access_guard' 'app.domains.telegram.headless' 'app.llm_config' 'app.rag.' 'app.catalog.service' 'app.domains.scanner.service'; do
    if rg -l --no-heading --color never -e "from $imp|import $imp" -g '*.py' "$TARGET" 2>/dev/null | grep -v '_vendor/' | grep -q .; then
      echo "FAIL: unreplaced import $imp" >&2
      FAIL=1
    fi
  done
  if rg -n --no-heading --color never -e '^from app\.|^import app\.' -g '*.py' "$TARGET/_vendor" 2>/dev/null | grep -vE '_vendor/(osint|chain_registry|rag_pkg|scanner_engines|unified_scanner|news_ingest|crypto_embeddings)/' | grep -q .; then
    echo "FAIL: module-level app.* imports remain in vendored files" >&2
    FAIL=1
  fi
  for v in settings_shim.py llm_config.py ai_guardrails.py shared_utils.py channels_registry.py redis_client.py logging_shim.py access_guard.py osint_tools.py dedup.py cache.py rug_risk_rules.py; do
    if [ -f "$TARGET/_vendor/$v" ]; then :; elif [ -f "$TARGET/$v" ]; then :; else
      echo "FAIL: missing vendor/bot file $v" >&2
      FAIL=1
    fi
  done
  [ -d "$TARGET/_vendor/rag_pkg" ] && [ -d "$TARGET/_vendor/scanner_engines" ] || {
    echo "FAIL: missing rag_pkg/scanner_engines" >&2
    FAIL=1
  }
  [ -f "$HERE/scripts/vendor-from-backend.sh" ] || {
    echo "FAIL: missing vendor script" >&2
    FAIL=1
  }
  # De-godding tripwires: a vendor refresh re-copies mapped files one-way and
  # would resurrect god modules that were split locally. Fail loudly instead.
  gmgn_lines="$(wc -l <"$TARGET/_vendor/gmgn_provider.py")"
  if [ "$gmgn_lines" -gt 200 ]; then
    echo "FAIL: _vendor/gmgn_provider.py regrew to ${gmgn_lines} lines (facade must stay <=200; split lives in gmgn_transport.py + gmgn_endpoints_*.py)" >&2
    FAIL=1
  fi
  ragdedup_lines="$(wc -l <"$TARGET/_vendor/rag_pkg/dedup.py")"
  if [ "$ragdedup_lines" -gt 80 ]; then
    echo "FAIL: _vendor/rag_pkg/dedup.py regrew to ${ragdedup_lines} lines (re-export shim only; canonical copy is _vendor/dedup.py)" >&2
    FAIL=1
  fi
  owm_lines="$(wc -l <"$TARGET/_vendor/scanner_engines/own_wallet_map.py")"
  if [ "$owm_lines" -gt 450 ]; then
    echo "FAIL: scanner_engines/own_wallet_map.py regrew to ${owm_lines} lines (facade must stay <=450; split lives in own_wallet_map_time.py + own_wallet_map_intel.py)" >&2
    FAIL=1
  fi
  lpr_lines="$(wc -l <"$TARGET/_vendor/scanner_engines/launchpad_resolver.py")"
  if [ "$lpr_lines" -gt 220 ]; then
    echo "FAIL: scanner_engines/launchpad_resolver.py regrew to ${lpr_lines} lines (facade must stay <=220; split lives in launchpad_registry.py + launchpad_resolve_chain.py + launchpad_resolve_universal.py)" >&2
    FAIL=1
  fi
  if [ "$FAIL" -eq 0 ]; then
    echo "OK: standalone bot + vendored modules (invariants hold)."
    exit 0
  fi
  exit 1
fi

echo "Refreshing vendored modules (rmi-backend -> _vendor) ..."
bash "$HERE/scripts/vendor-from-backend.sh"
echo "Refresh complete."
echo "Next: git add src/rugmunchbot && git commit -m 'chore(bot): refresh vendors'"
