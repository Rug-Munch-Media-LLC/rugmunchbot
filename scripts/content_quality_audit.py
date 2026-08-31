#!/usr/bin/env python3
"""Weekly content-quality audit — samples the past week's posts, lints them
against the style guide, and writes a Markdown report.

Report: /root/.knowledge/content-quality-audit-YYYY-MM-DD.md

Checks (deterministic, no LLM — see rugmunchbot.support.content_lint):
* banned phrases (style-guide anti-slop list)
* double-linking (same URL twice in one post)
* missing images (feed posts without an http(s) image_url)
* template-signature (rugbrain fallback writer output)
* bland commentary (< 20 words of original commentary)

Fail-soft: unreadable/empty store → report noting no data, exit 0 (the audit
is observational; CI's content-lint job is the blocking gate).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rugmunchbot.support.content_lint import lint_posts

FEED_DATA = os.getenv("RMI_FEED_DIR", "/var/www/feed.rugmunch.io") + "/data"
REPORT_DIR = os.getenv("CONTENT_AUDIT_DIR", "/root/.knowledge")
WINDOW_DAYS = 7


def _load_posts(path: str) -> list[dict[str, Any]]:
    """Tolerant read of the feed store (list or {"posts": [...]} wrapper)."""
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(loaded, dict):
        return [p for p in loaded.get("posts") or [] if isinstance(p, dict)]
    if isinstance(loaded, list):
        return [p for p in loaded if isinstance(p, dict)]
    return []


def _recent(posts: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    """Posts with a ts inside the audit window (undated posts are kept —
    their absence of a timestamp is itself worth seeing in the report)."""
    cutoff = now - timedelta(days=WINDOW_DAYS)
    out: list[dict[str, Any]] = []
    for post in posts:
        ts_raw = str(post.get("ts") or "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            out.append(post)  # unparsable ts → keep, flagged below
            continue
        if ts >= cutoff:
            out.append(post)
    return out


def main() -> int:
    now = datetime.now(UTC)
    posts = _recent(_load_posts(os.path.join(FEED_DATA, "posts.json")), now)

    lines: list[str] = [
        f"# Content Quality Audit — {now:%Y-%m-%d}",
        "",
        f"- Window: past {WINDOW_DAYS} days",
        f"- Posts sampled: {len(posts)}",
        f"- Source: `{FEED_DATA}/posts.json`",
        "",
    ]

    if not posts:
        lines.append("**No posts found in the window** — nothing to audit.")
        report = "\n".join(lines) + "\n"
        os.makedirs(REPORT_DIR, exist_ok=True)
        out_path = os.path.join(REPORT_DIR, f"content-quality-audit-{now:%Y-%m-%d}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"content-quality-audit: no posts; report → {out_path}")
        return 0

    findings = lint_posts(posts)
    total = sum(len(v) for v in findings.values())
    by_rule: dict[str, int] = {}
    for vs in findings.values():
        for v in vs:
            by_rule[v.rule] = by_rule.get(v.rule, 0) + 1

    lines.append(f"## Summary — {total} violation(s) across {len(findings)}/{len(posts)} posts")
    lines.append("")
    lines.append("| Rule | Count |")
    lines.append("|------|-------|")
    for rule, count in sorted(by_rule.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {rule} | {count} |")
    lines.append("")

    if findings:
        lines.append("## Findings")
        lines.append("")
        for slug, vs in findings.items():
            lines.append(f"### {slug}")
            for v in vs:
                lines.append(f"- `{v.rule}` {v.phrase} ({v.location}) — {v.detail}")
            lines.append("")
    else:
        lines.append("All sampled posts pass the style-guide lint. 🎉")

    report = "\n".join(lines) + "\n"
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, f"content-quality-audit-{now:%Y-%m-%d}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"content-quality-audit: {total} violation(s); report → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
