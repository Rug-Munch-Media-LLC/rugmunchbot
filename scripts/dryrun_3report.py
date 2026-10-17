#!/usr/bin/env python3
"""Dry-run the three-report engine END-TO-END — no Telegram post, no Ghost publish.

Exercises, per report: gather (limited) → write (Qwythos) → quality gate →
longform transform → Ghost-mirror PAYLOAD (built, never sent). Prints a
markdown sample. Also proves three-way distinctness and the guardrails.

Usage:  python3 scripts/dryrun_3report.py [--no-llm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


async def _dry_report(name: str, gather, build_messages, edition: str, task_class: str) -> dict:
    """Gather → write → gate → longform → mirror payload. Returns a result dict."""
    from rugmunchbot.channels._longform import to_markdown
    from rugmunchbot.channels._longform_lexical import to_lexical
    from rugmunchbot.channels.report_guardrails import report_guardrails

    out: dict = {"name": name, "edition": edition}
    _hr(f"[{name}] 1. GATHER")
    try:
        ctx = await gather()
    except Exception as exc:
        print(f"  gather FAILED: {exc!r}")
        out["error"] = f"gather:{exc!r}"
        return out
    sources = ctx.get("sources") or []
    print(f"  sources={sources}")
    for k in ("macro_stories", "recap_stories", "stories", "ranked_stories"):
        v = ctx.get(k)
        if v:
            print(f"  {k}: {len(v)} items")
            for s in v[:3]:
                print(f"    - {str(s.get('title') or s.get('content') or s)[:90]}")
    out["sources"] = sources

    _hr(f"[{name}] 2. WRITE (Qwythos lane, task_class={task_class})")
    from rugmunchbot.llm_config import chat_with_fallback

    messages = build_messages(ctx)
    try:
        raw = await chat_with_fallback(messages, "long", task_class=task_class)
    except Exception as exc:
        print(f"  write FAILED: {exc!r}")
        out["error"] = f"write:{exc!r}"
        return out
    print(f"  raw chars={len(raw)}")
    out["raw_chars"] = len(raw)

    from rugmunchbot.channels.midday_market_report._pipeline import cleanup_html, strip_echo

    report = cleanup_html(strip_echo(raw))
    print(f"  cleaned chars={len(report)}")
    out["report"] = report

    _hr(f"[{name}] 3. GUARDRAILS (recency + links)")
    ok, reasons = await report_guardrails(report, check_links=False)
    print(f"  recency ok={ok} reasons={reasons}")
    out["guardrails_ok"] = ok

    _hr(f"[{name}] 4. LONGFORM TRANSFORM (Telegram HTML → markdown)")
    md = to_markdown(report)
    print(f"  markdown chars={len(md)}")
    print("  --- markdown sample (first 900 chars) ---")
    print("\n".join("  " + ln for ln in md[:900].splitlines()))
    out["markdown"] = md

    _hr(f"[{name}] 5. GHOST-MIRROR PAYLOAD (built, NOT sent)")
    try:
        lex = to_lexical(report)
        doc = json.loads(lex)
        print(f"  lexical root children={len(doc.get('root', {}).get('children', []))}")
        out["lexical_ok"] = True
    except Exception as exc:
        print(f"  lexical FAILED: {exc!r}")
        out["lexical_ok"] = False
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM write step")
    args = ap.parse_args()

    from rugmunchbot.channels.midday_market_report import (
        EDITION as ME,
        build_messages as m_build,
        gather_midday_report_data as m_gather,
    )
    from rugmunchbot.channels.evening_wrap import (
        EDITION as EE,
        build_messages as e_build,
        gather_evening_wrap_data as e_gather,
    )

    results = []
    if not args.no_llm:
        results.append(await _dry_report("MIDDAY", m_gather, m_build, ME, "market_brief"))
        results.append(await _dry_report("EVENING", e_gather, e_build, EE, "recap"))
    else:
        print("--no-llm: skipping gather/write")

    _hr("THREE-WAY DISTINCTNESS PROOF")
    from rugmunchbot.channels._edition_dedup import (
        exclude_prior_editions,
        mark_edition_stories,
        story_hash,
    )

    stories = [
        {"title": "Fed holds rates steady", "url": "https://ex.com/fed"},
        {"title": "Token X rug pulls $4M", "url": "https://ex.com/rugx"},
        {"title": "KOL shills Token Y", "url": "https://ex.com/shill"},
    ]
    print("  story set:", [s["title"] for s in stories])
    # Morning claims all three.
    mark_edition_stories("morning", stories)
    print("  morning marked all 3")
    # Midday must exclude morning's.
    midday_kept = exclude_prior_editions(stories, before="midday")
    print(f"  midday sees {len(midday_kept)} (excluded {len(stories) - len(midday_kept)})")
    # Evening must exclude morning + midday.
    mark_edition_stories("midday", stories[:1])
    evening_kept = exclude_prior_editions(stories, before="evening")
    print(f"  evening sees {len(evening_kept)} (excluded {len(stories) - len(evening_kept)})")
    print(f"  hashes: {[story_hash(s)[:8] for s in stories]}")

    _hr("GUARDRAILS — recency gate + link validator")
    from rugmunchbot.channels.report_guardrails import recency_gate, validate_links

    old = "The exploit happened on 2026-01-05 and drained the pool."
    ok, reason = recency_gate(old, max_age_hours=48)
    print(f"  OLD claim  → ok={ok} reason={reason}")
    fresh = "Today, 2026-10-17, the pool was drained."
    ok2, reason2 = recency_gate(fresh, max_age_hours=48)
    print(f"  FRESH claim→ ok={ok2} reason={reason2}")
    dead = await validate_links("see https://httpstat.us/404 and https://example.com")
    print(f"  link validator dead={dead}")

    _hr("SCHEDULER")
    from rugmunchbot.channels.midday_market_report import midday_report_run_time
    from rugmunchbot.channels.evening_wrap import evening_wrap_run_time

    print(f"  midday  job time = {midday_report_run_time()}")
    print(f"  evening job time = {evening_wrap_run_time()}")

    _hr("SUMMARY")
    for r in results:
        print(
            f"  {r['name']}: sources={r.get('sources')} raw={r.get('raw_chars')} "
            f"guardrails_ok={r.get('guardrails_ok')} lexical_ok={r.get('lexical_ok')} "
            f"err={r.get('error')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
