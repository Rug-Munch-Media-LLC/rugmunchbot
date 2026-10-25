#!/usr/bin/env python3
"""CLI: score the RugMunch chat persona against the golden set.

  python3 scripts/eval_chat_persona.py --answers recorded.json   # offline
  python3 scripts/eval_chat_persona.py --live --gate             # real desk

Exit codes: 0 = every case passed, 1 = gate failure, 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rugmunchbot.persona._eval import load_cases, score_cases  # noqa: E402

DEFAULT_CASES = ROOT / "tests/fixtures/chat_eval_cases.json"
DEFAULT_ENDPOINT = "http://127.0.0.1:8000/api/v1/chat"
DEFAULT_REPORT = str(Path(tempfile.gettempdir()) / "chat_persona_eval.json")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--answers", help="JSON map case_id -> answer | {answer, latency_ms}")
    parser.add_argument("--live", action="store_true", help="call the knowledge desk")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--gate", action="store_true", help="exit 1 on any failure")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        print(f"cannot load cases: {exc}", file=sys.stderr)
        return 2
    answers = None
    if args.answers:
        try:
            answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"cannot load answers: {exc}", file=sys.stderr)
            return 2
    endpoint = None if answers is not None else (args.endpoint if args.live else None)
    if answers is None and endpoint is None:
        print("nothing to score: pass --answers or --live", file=sys.stderr)
        return 2

    rows, summary = score_cases(cases, answers, endpoint=endpoint, timeout=args.timeout)
    Path(args.report).write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8"
    )
    print(f"cases={summary['n']} pass={summary['pass_rate']}%")
    for name, rate in summary["rates"].items():
        print(f"  {name:<9} {rate}%")
    for category, bucket in sorted(summary["by_category"].items()):
        print(f"  {category:<13} {bucket['passed']}/{bucket['n']} ({bucket['pass_rate']}%)")
    print(f"  latency p50={summary['latency_p50_ms']}ms p95={summary['latency_p95_ms']}ms")
    if summary["failures"]:
        print(f"  failing: {', '.join(map(str, summary['failures']))}")
    print(f"report: {args.report}")
    return 1 if args.gate and summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
