#!/bin/bash
# standards/scripts/repo-boundary-check.sh — Repo Domain Boundary Guardrail
#
# PURPOSE: "Keep agents in their lanes." Given a repo + its staged file paths,
# assert each file falls within the repo's DECLARED domain (standards/repo-domains.json).
# A file whose domain belongs to a DIFFERENT repo is a boundary violation and FAILS
# the commit (pre-commit) / push (pre-push).
#
# Canonical source of truth: standards/repo-domains.json (+ standards/ARCHITECTURE.md).
# Installed per-repo: scripts/repo-boundary-check.sh (copy or symlink).
#
# USAGE (from repo root, via lefthook or CI):
#   bash scripts/repo-boundary-check.sh "path1.py path2.sol ..."
#   # if no args, reads staged files from git:
#   bash scripts/repo-boundary-check.sh
#
# BEHAVIOR:
#   - repo name derived from `git rev-parse --show-toplevel` basename.
#   - repo-domains.json located via: $REPO_DOMAINS_JSON -> ../standards/repo-domains.json
#     -> ./standards/repo-domains.json -> repo-local repo-domains.json.
#   - If the map or the current repo is absent from it -> SKIP (exit 0), so
#     non-fleet clones and repos not in the map never get a false block.
#   - Violations are listed with the owning repo; any violation => exit 1.
#
# NON-BLOCKING NOTE: a file that matches NO signature is allowed (generic files
# like README, Dockerfiles, configs are not domain-signalled and are not blocked).

set -uo pipefail

: "${REPO_DOMAINS_JSON:=}"

# ── Locate the canonical repo-domains map ──────────────────────────────
if [ -n "$REPO_DOMAINS_JSON" ] && [ -f "$REPO_DOMAINS_JSON" ]; then
    DOMAINS_JSON="$REPO_DOMAINS_JSON"
elif [ -f "../standards/repo-domains.json" ]; then
    DOMAINS_JSON="../standards/repo-domains.json"
elif [ -f "standards/repo-domains.json" ]; then
    DOMAINS_JSON="standards/repo-domains.json"
elif [ -f "repo-domains.json" ]; then
    DOMAINS_JSON="repo-domains.json"
else
    DOMAINS_JSON=""
fi

if [ -z "$DOMAINS_JSON" ]; then
    echo "[repo-boundary] repo-domains.json not found — SKIPPING (no boundary map)."
    exit 0
fi

# ── Determine current repo name ────────────────────────────────────────
REPO_NAME="$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")"

# ── Collect staged file paths ──────────────────────────────────────────
FILES=("$@")
# lefthook may pass {staged_files} as ONE space-joined arg (quoted) or as
# multiple args (unquoted). Normalize: if a single arg contains spaces, split it.
if [ "${#FILES[@]}" -eq 1 ] && [[ "${FILES[0]}" == *" "* ]]; then
    IFS=' ' read -r -a FILES <<<"${FILES[0]}"
fi
if [ "${#FILES[@]}" -eq 0 ]; then
    readarray -t FILES < <(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null | grep -v '^$' || true)
fi
if [ "${#FILES[@]}" -eq 0 ]; then
    echo "[repo-boundary] no changed files — OK"
    exit 0
fi

# ── Run the python boundary checker ────────────────────────────────────
python3 - "$DOMAINS_JSON" "$REPO_NAME" "${FILES[@]}" <<'PY'
import json, re, sys

map_path, repo_name = sys.argv[1], sys.argv[2]
files = sys.argv[3:]

with open(map_path) as fh:
    data = json.load(fh)
signatures = data.get("signatures", {})

if not signatures:
    print(f"[repo-boundary] no signatures declared in {map_path} — SKIPPING")
    sys.exit(0)
repos = data.get("repos", [])
# Only enforce for repos that participate in the boundary contract. A repo not
# listed opts out (so non-fleet clones / unrelated repos never get a false block).
if repos and repo_name not in repos:
    print(f"[repo-boundary] repo '{repo_name}' not in boundary contract — SKIPPING")
    sys.exit(0)


def glob_to_regex(pat):
    """Translate a gitignore-style glob (with ** / * / ?) to an anchored regex."""
    res, i, n = [], 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                j = i + 2
                if j < n and pat[j] == "/":
                    # '**/' -> matches zero or more leading directories
                    res.append("(?:.*/)?")
                    i = j + 1
                else:
                    # '**' -> matches anything incl. '/'
                    res.append(".*")
                    i = j
            else:
                res.append("[^/]*")
                i += 1
        elif c == "?":
            res.append("[^/]")
            i += 1
        elif c == "/":
            res.append("/")
            i += 1
        else:
            res.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(res) + "$")


# Pre-compile patterns
compiled = []
for sig, owner in signatures.items():
    try:
        compiled.append((glob_to_regex(sig), sig, owner))
    except re.error:
        continue

violations = []
for f in files:
    # Co-ownership: a file is allowed if the CURRENT repo owns ANY one of the
    # matching signatures. It is a violation only when every matching signature
    # points at a DIFFERENT repo (file-type/domain clearly belongs elsewhere).
    matching = [(sig, owner) for rx, sig, owner in compiled if rx.match(f)]
    if not matching:
        continue
    owners = {owner for _, owner in matching}
    if repo_name not in owners:
        # Pick the most specific owner to report (longest signature wins).
        best_sig, best_owner = max(matching, key=lambda t: len(t[0]))
        violations.append((f, best_sig, best_owner))

if violations:
    print(f"[repo-boundary] ⚠ '{repo_name}' has files that belong to a DIFFERENT repo's domain:")
    for f, sig, owner in violations:
        print(f"    {f}   (signature '{sig}' -> owned by '{owner}')")
    print("    → Move these to the owning repo, or update standards/repo-domains.json.")
    sys.exit(1)

print("[repo-boundary] OK — all staged files within declared domain"
      + (" for repo '" + repo_name + "'" if repo_name else ""))
sys.exit(0)
PY
