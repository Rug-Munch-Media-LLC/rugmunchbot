#!/usr/bin/env bash
# tia-pilot.sh — measure how much of the suite a diff actually needs.
#
# pytest-testmon keeps a per-test dependency map (.testmondata) and re-runs
# only the tests whose executed lines changed. This script reports the numbers
# and nothing else: it is NOT a merge gate and NOT a coverage source.
#
# FAIL-OPEN GUARANTEE: no .testmondata (cold cache, new branch, first run) =>
# the complete suite runs. A missing map can only make the run bigger, never
# smaller.
#
# COVERAGE HONESTY: selection changes what is measured. A selected run's total
# coverage and a diff-cover over it are both meaningless (untouched tests are
# never imported). The full nightly job (nightly.yml:test-full) owns coverage
# truth; this script prints selected-vs-full counts so the gap is visible
# instead of implied.
set -euo pipefail

full_total="$(python -m pytest tests/ --collect-only -q 2>/dev/null | grep -oE '^[0-9]+' | tail -1 || true)"
full_total="${full_total:-unknown}"

flag=()
mode="full (no .testmondata — fail-open)"
if [[ -f .testmondata ]]; then
	flag=(--testmon)
	mode="testmon"
fi

start="$(date +%s)"
set +e
out="$(python -m pytest tests/ -q --tb=short "${flag[@]}" 2>&1)"
rc=$?
set -e
elapsed=$(($(date +%s) - start))

selected="$(printf '%s\n' "$out" | grep -oE '[0-9]+ passed' | tail -1 | grep -oE '^[0-9]+' || true)"
deselected="$(printf '%s\n' "$out" | grep -oE '[0-9]+ deselected' | tail -1 | grep -oE '^[0-9]+' || true)"

{
	echo "mode=$mode"
	echo "collected_full=$full_total"
	echo "selected_passed=${selected:-unknown}"
	echo "deselected=${deselected:-0}"
	echo "wall_clock_s=$elapsed"
	echo "exit_code=$rc"
} | tee "${GITHUB_STEP_SUMMARY:-/dev/stdout}" 2>/dev/null || true

echo "::notice::TIA pilot mode=${mode} full=${full_total} selected=${selected:-unknown} deselected=${deselected:-0} wall=${elapsed}s"
exit "$rc"
