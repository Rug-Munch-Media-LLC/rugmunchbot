#!/usr/bin/env bash
# ruff over staged *.py — SKIPS src/rugmunchbot (byte-identical snapshot of
# the LIVE bot; ruff 0.15.x rewrites explicitly-passed files even when they
# are excluded in pyproject.toml, so filtering must happen here).
set -euo pipefail

files=()
for f in "$@"; do
  case "$f" in
  src/rugmunchbot/*) continue ;;
  *) files+=("$f") ;;
  esac
done

if [ ${#files[@]} -eq 0 ]; then
  echo "ruff: no lintable files (src/rugmunchbot snapshot excluded)"
  exit 0
fi

ruff check --fix "${files[@]}"
ruff format "${files[@]}"
