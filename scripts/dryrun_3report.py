#!/usr/bin/env python3
"""Dry-run the THREE daily reports END-TO-END — no Telegram post, no Ghost publish.

Per edition (Morning / Midday / Evening) this exercises: gather → write →
quality gate inputs → longform transform → GRAPHIC resolution → FEED payload
(the exact ``mirror_post_to_feed`` writer, into a throwaway store).

Proof printed per edition: dateline ("data as of … UTC"), commentary word
count, resolved graphic URL + HTTP content-type, and the stored feed entry
(title + image preserved).

Usage:  python3 scripts/dryrun_3report.py [--no-llm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def _http_content_type(url: str) -> str:
    """HEAD (GET fallback) → 'ok image/jpeg 725626B' or 'FAIL <reason>'."""
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(  # noqa: S310 - brand URL only
                url, method=method, headers={"User-Agent": "Mozilla/5.0 (rugmunchbot proof)"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310 - brand URL only
                ctype = resp.headers.get("Content-Type", "")
                size = resp.headers.get("Content-Length", "?")
                return f"{resp.status} {ctype} {size}B"
        except Exception as exc:
            last = repr(exc)[:90]
    return f"FAIL {last}"


def _graphic_proof(post_type: str, override: str) -> dict:
    from rugmunchbot.support.image_policy import is_valid_image
    from rugmunchbot.support.post_graphics import graphic_for_type

    url = graphic_for_type(post_type, override)
    valid = is_valid_image(url)
    print(f"  graphic[{post_type}] = {url}")
    print(f"    policy_valid={valid} served={_http_content_type(url)}")
    return {"url": url, "valid": valid}


def _feed_proof(report: str, image_url: str, edition: str) -> dict:
    """Write through the production writer into a temp store; read it back."""
    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

    with tempfile.TemporaryDirectory() as tmp:
        slug = mirror_post_to_feed(
            {
                "text": report,
                "story_ref": f"dryrun-{edition.lower()}",
                "source": "rugmunch.io",
                "kind": "briefing",
                "title": edition,
                "tags": ["briefing", edition.lower()],
                "image_url": image_url,
            },
            store_dir=tmp,
        )
        stored = json.loads((Path(tmp) / "posts.json").read_text())[0]
    ok = bool(slug) and bool(stored.get("title")) and bool(stored.get("image"))
    print(
        f"  feed slug={slug} title={stored.get('title')!r} image_kept={bool(stored.get('image'))}"
    )
    return {"slug": slug, "feed_ok": ok, "stored": stored}


async def _dry_report(
    name: str,
    post_type: str,
    graphic_override: str,
    gather,
    build_messages,
    edition: str,
    task_class: str,
    *,
    use_llm: bool,
) -> dict:
    from rugmunchbot.channels._longform import to_markdown
    from rugmunchbot.channels.midday_market_report._pipeline import cleanup_html, strip_echo
    from rugmunchbot.channels.report_guardrails import report_guardrails

    out: dict = {"name": name, "edition": edition}
    _hr(f"[{name}] 1. GATHER")
    try:
        ctx = await gather()
    except Exception as exc:
        print(f"  gather FAILED: {exc!r}")
        return {**out, "error": f"gather:{exc!r}"}
    print(f"  sources={ctx.get('sources')}")
    out["sources"] = ctx.get("sources")

    _hr(f"[{name}] 2. WRITE (task_class={task_class})")
    report = ""
    if use_llm:
        from rugmunchbot.llm_config import chat_with_fallback

        try:
            raw = await chat_with_fallback(build_messages(ctx), "long", task_class=task_class)
            report = cleanup_html(strip_echo(raw))
        except Exception as exc:
            print(f"  write FAILED: {exc!r}")
            return {**out, "error": f"write:{exc!r}"}
    print(f"  chars={len(report)}")
    out["chars"] = len(report)

    _hr(f"[{name}] 3. EDITORIAL PROOF (dateline · commentary)")
    words = len(report.split())
    dateline = "data as of" in report and "UTC" in report
    print(f"  words={words} dateline_ok={dateline}")
    out["dateline_ok"] = dateline
    if report:
        ok, reasons = await report_guardrails(report, check_links=False)
        print(f"  recency ok={ok} reasons={reasons}")
        print(f"  longform chars={len(to_markdown(report))}")

    _hr(f"[{name}] 4. GRAPHIC")
    graphic = _graphic_proof(post_type, graphic_override)
    out.update(graphic)

    _hr(f"[{name}] 5. FEED (same commentary + graphic)")
    if report:
        out["feed"] = _feed_proof(report, graphic["url"], edition)
    else:
        print("  skipped (no report body)")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="gather + graphic/feed proof only")
    args = ap.parse_args()

    from rugmunchbot.channels.daily_rug import (
        EDITION as DE,
        build_messages as d_build,
        gather_daily_rug_data as d_gather,
    )
    from rugmunchbot.channels.evening_wrap import (
        EDITION as EE,
        EVENING_WRAP_GRAPHIC,
        build_messages as e_build,
        gather_evening_wrap_data as e_gather,
    )
    from rugmunchbot.channels.midday_market_report import (
        EDITION as ME,
        MIDDAY_REPORT_GRAPHIC,
        build_messages as m_build,
        gather_midday_report_data as m_gather,
    )

    specs = (
        ("MORNING", "daily_rug", "", d_gather, d_build, DE, "market_brief"),
        ("MIDDAY", "midday_report", MIDDAY_REPORT_GRAPHIC, m_gather, m_build, ME, "market_brief"),
        ("EVENING", "evening_wrap", EVENING_WRAP_GRAPHIC, e_gather, e_build, EE, "recap"),
    )
    results = [
        await _dry_report(*spec, use_llm=not args.no_llm)
        for spec in specs  # type: ignore[misc]
    ]

    _hr("SUMMARY — three editions")
    for r in results:
        print(
            f"  {r['name']}: sources={r.get('sources')} chars={r.get('chars')} "
            f"dateline={r.get('dateline_ok')} graphic_valid={r.get('valid')} "
            f"feed_ok={(r.get('feed') or {}).get('feed_ok')} err={r.get('error')}"
        )
    failed = [
        r
        for r in results
        if r.get("error")
        or r.get("valid") is False
        or (r.get("chars") and r.get("dateline_ok") is False)
        or (r.get("chars") and not (r.get("feed") or {}).get("feed_ok"))
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
