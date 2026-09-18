#!/usr/bin/env bash
# check-policy.sh — rugmunchbot good-code policy gate.
# Canonical policy: docs/GOOD-CODE-POLICY.md
# Scope: first-party code (src/rugmunchbot excluding _vendor/, tests/).
# Exit != 0 = policy violation. WARN lines are reported but do not fail.
set -uo pipefail
cd "$(dirname "$0")/.."

FAIL=0
warn() { printf 'WARN: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*"; FAIL=1; }

trim_prose() { grep -vE ':[0-9]+:[[:space:]]*#' | grep -v '``' || true; }

# shellcheck source=scripts/policy-budgets.env
[ -f scripts/policy-budgets.env ] && source scripts/policy-budgets.env

# ── 1. band-aid markers (first-party only) ──────────────────────────────────
OUT=$(grep -rnE '#[[:space:]]*(HACK|WORKAROUND|BANDAID|XXX|TEMP)\b' src/rugmunchbot tests \
  --include='*.py' 2>/dev/null | grep -v '_vendor/' || true)
[ -n "$OUT" ] && { fail "band-aid markers found (HACK/WORKAROUND/BANDAID/XXX/TEMP):"; echo "$OUT" | head -20; }

# ── 2. runtime patching in src (code only; comment/docstring prose filtered) ─
OUT=$(grep -rnE 'monkeypatch\.(setattr|delattr|setitem|context)|(^|[^#[:alnum:]_])setattr\(' src/rugmunchbot \
  --include='*.py' 2>/dev/null | grep -v '_vendor/' | trim_prose || true)
[ -n "$OUT" ] && { fail "runtime patching in src (monkeypatching/setattr):"; echo "$OUT" | head -20; }

# ── 3. sys.path / importlib hot patches in src ──────────────────────────────
OUT=$(grep -rnE 'sys\.path\.(insert|append)|importlib\.reload' src/rugmunchbot --include='*.py' 2>/dev/null \
  | grep -v '_vendor/' | trim_prose || true)
[ -n "$OUT" ] && { fail "sys.path/importlib hot patches in src:"; echo "$OUT" | head -20; }
OUT=$(grep -rn 'importlib\.import_module' src/rugmunchbot --include='*.py' 2>/dev/null \
  | grep -v '_vendor/' | trim_prose || true)
[ -n "$OUT" ] && warn "dynamic import (importlib.import_module) in src — prefer a static registry: $(printf '%s' "$OUT" | grep -c . ) site(s)"

# ── 4. god files (>500 lines, first-party; allowlist = grandfathered) ──────
while read -r n f; do
  [ -z "${f:-}" ] && continue
  case " ${GOD_FILE_ALLOW:-} " in
    *" $f "*) warn "god file (allowlisted, split pending): $f ($n lines)" ;;
    *)        fail "god file >500 lines (split by responsibility; 500 is the standard): $f ($n lines)" ;;
  esac
done < <(find src/rugmunchbot -name '*.py' -not -path '*/_vendor/*' -exec wc -l {} + \
  | awk '$1 > 500 && $2 != "total" {print $1, $2}')

# ── 5. dead/legacy artifacts ────────────────────────────────────────────────
OUT=$(find . -path ./.venv -prune -o \( -name '*.bak' -o -name '*.bak-*' -o -name '*_old.py' \) -print 2>/dev/null | head -10)
[ -n "$OUT" ] && { fail "dead artifacts (.bak/_old):"; echo "$OUT"; }

# ── 6. silent swallows (informational) ─────────────────────────────────────
OUT=$(grep -rnPzo 'except[^\n]*:\n[[:space:]]*pass[ \t]*\n' src/rugmunchbot --include='*.py' 2>/dev/null \
  | tr '\0' '\n' | grep -v '_vendor/' | grep -vE ':[0-9]+:[[:space:]]*#' || true)
[ -n "$OUT" ] && warn "silent except…: pass (add a log or a handled-state comment — see policy §1)"

# ── 7. suppression ratchets (may go down, never up) ────────────────────────
count() { grep -rc "$1" src/rugmunchbot --include='*.py' 2>/dev/null | awk -F: '{s+=$2} END {print s+0}'; }
check_budget() { # name, current, budget
  local name="$1" cur="$2" budget="$3"
  if [ "$cur" -gt "$budget" ]; then
    fail "$name count $cur > budget $budget (ratchet: may go down, never up — update scripts/policy-budgets.env when reducing)"
  else
    printf 'ok: %s %s/%s\n' "$name" "$cur" "$budget"
  fi
}
check_budget "noqa" "$(count 'noqa')" "${NOQA_BUDGET:-9999}"
check_budget "type-ignore" "$(count 'type: ignore')" "${TYPE_IGNORE_BUDGET:-9999}"
check_budget "pragma-no-cover" "$(count 'pragma: no cover')" "${PRAGMA_NO_COVER_BUDGET:-9999}"

# ── 8. monkeypatch ratchet (src pinned at 0; tests may only go down) ───────
MP_SRC=$(grep -rnE 'monkeypatch\.(setattr|delattr|setitem)' src/rugmunchbot --include='*.py' 2>/dev/null \
  | grep -v '_vendor/' | trim_prose | grep -c 'monkeypatch' || true)
if [ "${MP_SRC:-0}" -gt 0 ]; then
  fail "monkeypatch call sites in src/ — must be 0 (got $MP_SRC)"
else
  printf 'ok: src monkeypatch 0/0\n'
fi
MP_TESTS=$(grep -rc 'monkeypatch' tests --include='*.py' 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')
check_budget "tests monkeypatch refs" "$MP_TESTS" "${MONKEYPATCH_TEST_BUDGET:-99999}"

echo
if [ "$FAIL" -ne 0 ]; then
  echo "POLICY FAIL — see docs/GOOD-CODE-POLICY.md"
  exit 1
fi
echo "POLICY OK — first-party code is clean."
