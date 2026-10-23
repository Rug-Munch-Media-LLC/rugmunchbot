#!/usr/bin/env python3
"""Render ONE daily report from real gathered data, then prove the feed carry.

Used as the live-render proof when the LLM write lane is unavailable: it walks
the real gatherer for the requested edition, builds the masthead (with the UTC
dateline), writes paragraphs strictly from the gathered facts, resolves the
edition graphic, checks the served content type, and pushes the body through
the PRODUCTION feed writer into a throwaway store.

Usage:
    python3 scripts/render_daily_report.py --edition morning
    python3 scripts/render_daily_report.py --edition midday --store /tmp/proof
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ASSETS = ("bitcoin", "ethereum", "solana")


def _content_type(url: str) -> str:
    """HEAD (GET fallback) → '200 image/jpeg 725626B' or 'FAIL <reason>'."""
    last = "no attempt"
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(  # noqa: S310 - brand URL only
                url,
                method=method,
                headers={"User-Agent": "Mozilla/5.0 (rugmunchbot render proof)"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:  # noqa: S310
                return (
                    f"{resp.status} {resp.headers.get('Content-Type', '')} "
                    f"{resp.headers.get('Content-Length', '?')}B"
                )
        except Exception as exc:
            last = repr(exc)[:90]
    return f"FAIL {last}"


def _prices_paragraph(prices: object) -> str:
    """Real spot + 24h move, one sentence per asset, or '' when absent."""
    if not isinstance(prices, dict) or not prices:
        return ""
    parts = []
    for asset in ASSETS:
        row = prices.get(asset)
        if not isinstance(row, dict) or row.get("usd") is None:
            continue
        change = row.get("usd_24h_change")
        move = f", {change:+.2f}% on 24h" if isinstance(change, (int, float)) else ""
        parts.append(f"{asset.capitalize()} trades at ${row['usd']:,.2f}{move}")
    return ". ".join(parts) + "." if parts else ""


def _stories_paragraph(ctx: dict) -> str:
    """Up to three real headlines with their outlet, links only when present."""
    rows: list = []
    for key in ("ranked_stories", "macro_stories", "recap_stories", "stories"):
        rows = ctx.get(key) or []
        if rows:
            break
    lines = []
    for story in rows[:3]:
        if not isinstance(story, dict):
            continue
        title = str(story.get("title") or story.get("content") or "").strip()
        if not title:
            continue
        url = str(story.get("url") or "").strip()
        source = str(story.get("source") or "").strip()
        ref = f'<a href="{url}">{source or "source"}</a>' if url else (source or "unsourced")
        lines.append(f"• {title[:180]} — {ref}")
    return "\n".join(lines)


def _intel_paragraph(ctx: dict) -> str:
    """Honest one-liner about what the gatherer actually returned."""
    counts = {k: len(v) for k, v in ctx.items() if isinstance(v, list) and k != "sources" and v}
    tally = ", ".join(f"{k}={n}" for k, n in counts.items()) or "no intel rows"
    return f"Gatherer output for this edition: {tally}."


def _render_body(ctx: dict, edition: str, graphic: str) -> str:
    """Masthead + dateline + real-data paragraphs (no generated prose)."""
    stamp = datetime.fromisoformat(str(ctx["generated_at"])).astimezone(UTC)
    dateline = stamp.strftime("%A, %B %d, %Y · data as of %H:%M UTC")
    blocks = [f"<b>━━━ {edition} ━━━</b>", f"<i>{dateline} · Crypto Rug Muncher desk</i>"]
    prices = _prices_paragraph(ctx.get("prices"))
    if prices:
        blocks.append(prices)
    stories = _stories_paragraph(ctx)
    if stories:
        blocks.append(stories)
    blocks.append(_intel_paragraph(ctx))
    blocks.append(
        "Rendered from gathered data only — the LLM write lane was unavailable for "
        f"this proof run. Graphic: {graphic}"
    )
    return "\n\n".join(blocks)


def _spec(edition: str) -> tuple:
    """(module, post_type, graphic override) for one edition."""
    from rugmunchbot.channels import daily_rug, evening_wrap, midday_market_report

    if edition == "morning":
        return daily_rug, "daily_rug", "", daily_rug.gather_daily_rug_data
    if edition == "midday":
        return (
            midday_market_report,
            "midday_report",
            midday_market_report.MIDDAY_REPORT_GRAPHIC,
            midday_market_report.gather_midday_report_data,
        )
    return (
        evening_wrap,
        "evening_wrap",
        evening_wrap.EVENING_WRAP_GRAPHIC,
        evening_wrap.gather_evening_wrap_data,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", default="morning", choices=("morning", "midday", "evening"))
    ap.add_argument("--store", default="", help="feed store dir (default: throwaway)")
    args = ap.parse_args()

    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed
    from rugmunchbot.support.image_policy import is_valid_image
    from rugmunchbot.support.post_graphics import graphic_for_type

    module, post_type, override, gather = _spec(args.edition)
    ctx = await gather()
    ctx.setdefault("generated_at", datetime.now(UTC).isoformat())

    url = graphic_for_type(post_type, override)
    body = _render_body(ctx, module.EDITION, url)
    paragraphs = [p for p in body.split("\n\n") if p.strip()]

    print(f"edition      : {module.EDITION}")
    print(f"dateline     : {body.splitlines()[1]}")
    print(f"paragraphs   : {len(paragraphs)}  words={len(body.split())}  chars={len(body)}")
    print(f"graphic      : {url}")
    print(f"graphic_valid: {is_valid_image(url)}  served={_content_type(url)}")

    store = args.store or tempfile.mkdtemp(prefix="render-proof-")
    slug = mirror_post_to_feed(
        {
            "text": body,
            "story_ref": f"render-proof-{args.edition}",
            "source": "rugmunch.io",
            "kind": "briefing",
            "title": module.EDITION,
            "tags": ["briefing", args.edition],
            "image_url": url,
        },
        store_dir=store,
    )
    stored = json.loads((Path(store) / "posts.json").read_text())[0]
    print(f"feed slug    : {slug}")
    print(f"feed title   : {stored.get('title')!r}")
    print(f"feed image   : {stored.get('image')}")
    print(f"feed chars   : {len(stored.get('text', ''))}")
    print(f"feed store   : {store}")

    ok = bool(
        slug
        and stored.get("title") == module.EDITION
        and stored.get("image") == url
        and len(paragraphs) > 2
        and "data as of" in body
    )
    print(f"RESULT       : {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
