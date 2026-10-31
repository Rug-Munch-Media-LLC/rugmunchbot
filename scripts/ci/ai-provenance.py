"""AI provenance + risk scoring for pull requests: reads AI-* trailers
(AI-Authored / AI-Model / AI-Agent / AI-Tool), classifies the diff, scores risk and
renders a PR comment block; never reads prompt content.

score = path_class + diff_size + test_gap (0..100; high >= 70). path_class is 40 for
money|auth|payment|x402|billing, 15 source, 10 other, 0 docs-or-tests-only;
diff_size = min(30, lines // 20); test_gap = 30 (high-risk, 0 tests), 15 (test ratio
< 20%), else 0. BLOCKING <=> high > 0 and score >= 70 and tests == 0; docs-only and
tests-only diffs are always advisory.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import NamedTuple

MARKER = "<!-- ai-provenance -->"
HIGH_RISK = re.compile(
    r"(^|/)(money|auth|payments?|billing|x402)(/|$)"
    r"|(^|/)(auth|payment|billing|x402)[_-]"
    r"|(^|/)(wallets?|signing|keys?|ledger)(/|$)",
    re.I,
)
TEST_FILE = re.compile(
    r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]+\.py$|_test\.py$|\.(test|spec)\.[cm]?[jt]sx?$", re.I
)
DOCS_FILE = re.compile(r"\.(md|mdx|rst|txt)$|(^|/)docs?/", re.I)
SOURCE_FILE = re.compile(r"\.(py|ts|tsx|js|jsx|mjs|go|rs|sol|vue|sql)$", re.I)
TRAILER = re.compile(r"^AI-(Authored|Model|Agent|Tool):[ \t]*(.+?)[ \t]*$", re.I | re.M)
_PATTERNS = ((TEST_FILE, "test"), (DOCS_FILE, "docs"), (HIGH_RISK, "high"), (SOURCE_FILE, "source"))
GIT = shutil.which("git") or "git"


class DiffStats(NamedTuple):
    files: tuple[str, ...]
    added: int
    removed: int
    high: int
    tests: int
    docs: int
    sources: int

    @property
    def changed(self) -> int:
        return len(self.files)

    @property
    def docs_only(self) -> bool:
        return 0 < self.changed == self.docs

    @property
    def tests_only(self) -> bool:
        return 0 < self.changed == self.tests + self.docs


class Risk(NamedTuple):
    score: int
    level: str
    blocking: bool
    path_pts: int
    size_pts: int
    gap_pts: int


def _git(*args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted input
        (GIT, *args), capture_output=True, text=True, check=False
    )
    return done.stdout


def _classify(path: str) -> str:
    return next((kind for pattern, kind in _PATTERNS if pattern.search(path)), "other")


def _collect_trailers(base: str, head: str) -> tuple[str, set[str], set[str]]:
    authored, models, agents = "no", set(), set()
    for blob in _git("log", "--format=%B%x00", f"{base}..{head}").split("\x00"):
        for key, val in TRAILER.findall(blob):
            val = val.strip()[:80]
            if key.lower() == "authored" and val.lower() in {"yes", "true", "1"}:
                authored = "yes"
            elif key.lower() == "model":
                models.add(val)
            else:
                agents.add(val)
    return authored, models, agents


def _parse_numstat(text: str) -> DiffStats:
    files: list[str] = []
    added = removed = high = tests = docs = sources = 0
    for line in text.splitlines():
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
        sources += kind in {"high", "source"}
    return DiffStats(tuple(files), added, removed, high, tests, docs, sources)


def _points(stats: DiffStats) -> tuple[int, int]:
    """Return (path_class points, test_gap points)."""
    docs_or_tests = stats.docs_only or stats.tests_only
    path_pts = 0 if docs_or_tests else (40 if stats.high else (15 if stats.sources else 10))
    if not stats.high:
        return path_pts, 0
    if stats.tests == 0:
        return path_pts, 30
    ratio = stats.tests * 100 // max(stats.changed, 1)
    return path_pts, (15 if ratio < 20 else 0)


def _score(stats: DiffStats, mode: str) -> Risk:
    path_pts, gap_pts = _points(stats)
    size_pts = min(30, (stats.added + stats.removed) // 20)
    score = min(100, path_pts + size_pts + gap_pts)
    blocking = bool(mode == "gate" and stats.high and score >= 70 and stats.tests == 0)
    level = "high" if score >= 70 else ("medium" if score >= 40 else "low")
    return Risk(score, level, blocking, path_pts, size_pts, gap_pts)


def _render(authored: str, models: set[str], agents: set[str], stats: DiffStats, risk: Risk) -> str:
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
            f"| files touched | {stats.changed} ({stats.tests} test, {stats.docs} docs) |",
            f"| diff | +{stats.added} / -{stats.removed} lines |",
            f"| risk score | **{risk.score}/100** ({risk.level}) |",
            f"| score inputs | path_class={risk.path_pts} + diff_size={risk.size_pts} + "
            f"test_gap={risk.gap_pts} |",
            f"| mode | {('BLOCKING' if risk.blocking else 'advisory')} |",
            "",
        ]
    )
    if risk.blocking:
        block += (
            "**Blocked:** high-risk path (`money|auth|payment|x402|billing`) "
            "changed with no test file and risk >= 70.\n"
            "Add or update a test in this PR, or get a human reviewer to "
            "apply the `ai-review:override` label.\n"
        )
    return block


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--mode", choices=["advisory", "gate"], default="gate")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    authored, models, agents = _collect_trailers(args.base, args.head)
    stats = _parse_numstat(_git("diff", "--numstat", "--no-renames", f"{args.base}...{args.head}"))
    risk = _score(stats, args.mode)
    block = _render(authored, models, agents, stats, risk)
    review_needed = (not stats.docs_only) and stats.changed > 0
    outputs = {
        "risk_score": str(risk.score),
        "risk_level": risk.level,
        "blocking": "true" if risk.blocking else "false",
        "review_needed": "true" if review_needed else "false",
        "ai_authored": authored,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(block)
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.writelines(f"{k}={v}\n" for k, v in outputs.items())
    print(block)
    print(json.dumps(outputs, sort_keys=True))
    return 1 if risk.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
