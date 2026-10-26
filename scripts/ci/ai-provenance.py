#!/usr/bin/env python3
"""AI provenance + risk scoring for pull requests.

Reads AI-* git trailers emitted by the commit hook (AI-Authored / AI-Model /
AI-Agent / AI-Tool), classifies the diff, scores risk and renders a PR comment
block. It never reads prompt content and never prints module arguments.

Risk rule (deterministic, no model involved):
  score = path_class + diff_size + test_gap     (0..100, high when >= 70)
    path_class  : money|auth|payment|x402|billing path -> 40
                  source file                          -> 15
                  any other non-docs/non-test file      -> 10
                  docs-only or tests-only diff          ->  0
    diff_size   : min(30, (added + removed) // 20)
    test_gap    : high-risk path touched and 0 test files changed -> 30
                  high-risk path and test ratio < 20%             -> 15
                  otherwise                                       ->  0
  BLOCKING  <=> high_risk_paths > 0 and score >= 70 and tests_changed == 0
  Docs-only and tests-only diffs are ALWAYS advisory, never blocking.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

MARKER = "<!-- ai-provenance -->"
HIGH_RISK = re.compile(
    r"(^|/)(money|auth|payments?|billing|x402)(/|$)"
    r"|(^|/)(auth|payment|billing|x402)[_-]"
    r"|(^|/)(wallets?|signing|keys?|ledger)(/|$)",
    re.I,
)
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]+\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$", re.I)
DOCS_FILE = re.compile(r"\.(md|mdx|rst|txt)$|(^|/)docs?/", re.I)
SOURCE_FILE = re.compile(r"\.(py|ts|tsx|js|jsx|mjs|go|rs|sol|vue|sql)$", re.I)
TRAILER = re.compile(r"^AI-(Authored|Model|Agent|Tool):[ \t]*(.+?)[ \t]*$", re.I | re.M)


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return done.stdout


def _classify(path: str) -> str:
    if TEST_FILE.search(path):
        return "test"
    if DOCS_FILE.search(path):
        return "docs"
    if HIGH_RISK.search(path):
        return "high"
    if SOURCE_FILE.search(path):
        return "source"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--mode", choices=["advisory", "gate"], default="gate")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    authored, models, agents = "no", set(), set()
    for blob in _git("log", "--format=%B%x00", f"{args.base}..{args.head}").split("\x00"):
        for key, val in TRAILER.findall(blob):
            val = val.strip()[:80]
            if key.lower() == "authored" and val.lower() in {"yes", "true", "1"}:
                authored = "yes"
            elif key.lower() == "model":
                models.add(val)
            else:
                agents.add(val)

    files: list[str] = []
    added = removed = high = tests = docs = 0
    for line in _git("diff", "--numstat", "--no-renames", f"{args.base}...{args.head}").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        add, rem, path = parts
        added += int(add) if add.isdigit() else 0
        removed += int(rem) if rem.isdigit() else 0
        kind = _classify(path)
        files.append(f"{kind}:{path}")
        high += kind == "high"
        tests += kind == "test"
        docs += kind == "docs"

    changed = len(files)
    lines = added + removed
    docs_only = changed > 0 and docs == changed
    tests_only = changed > 0 and (tests + docs) == changed
    any_source = any(f.startswith(("high:", "source:")) for f in files)

    size_pts = min(30, lines // 20)
    path_pts = 0 if (docs_only or tests_only) else (40 if high else (15 if any_source else 10))
    if high and tests == 0:
        gap_pts = 30
    elif high and (tests * 100 // max(changed, 1)) < 20:
        gap_pts = 15
    else:
        gap_pts = 0
    score = min(100, path_pts + size_pts + gap_pts)
    level = "high" if score >= 70 else ("medium" if score >= 40 else "low")
    blocking = bool(args.mode == "gate" and high and score >= 70 and tests == 0)
    review_needed = (not docs_only) and changed > 0

    reasons = [f"path_class={path_pts}", f"diff_size={size_pts}", f"test_gap={gap_pts}"]
    block = "\n".join(
        [
            MARKER,
            "## AI provenance & risk",
            "",
            "| field | value |",
            "| --- | --- |",
            f"| `AI-AUTHORED` | **{authored}** |",
            f"| model(s) | {', '.join(sorted(models)) or 'n/a'} |",
            f"| agent/tool | {', '.join(sorted(agents)) or 'n/a'} |",
            f"| files touched | {changed} ({tests} test, {docs} docs) |",
            f"| diff | +{added} / -{removed} lines |",
            f"| risk score | **{score}/100** ({level}) |",
            f"| score inputs | {' + '.join(reasons)} |",
            f"| mode | {('BLOCKING' if blocking else 'advisory')} |",
            "",
        ]
    )
    if blocking:
        block += ("**Blocked:** high-risk path (`money|auth|payment|x402|billing`) "
                  "changed with no test file and risk >= 70.\n"
                  "Add or update a test in this PR, or get a human reviewer to "
                  "apply the `ai-review:override` label.\n")

    path = args.out
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(block)
    outputs = {
        "risk_score": str(score),
        "risk_level": level,
        "blocking": "true" if blocking else "false",
        "review_needed": "true" if review_needed else "false",
        "ai_authored": authored,
    }
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.writelines(f"{k}={v}\n" for k, v in outputs.items())
    print(block)
    print(json.dumps(outputs, sort_keys=True))
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
