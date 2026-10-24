"""Formatting for the daily market briefs: edition naming, the writer
prompt, message construction, HTML cleanup, and Telegram splitting."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.channels.daily_market_summary._assemble import _serialize_context
from rugmunchbot.channels.daily_market_summary._config import DAILY_MARKET_SUMMARY_MAX_CHARS

#: Edition names for the two daily market briefs (branded 2026-08-31).
#: The morning brief is the flagship "The Morning Rug" (07:00 ET); the
#: midday brief re-runs the same pipeline on fresher data as
#: "Midday Market Update" (13:00 ET). Both share this prompt — the edition
#: name is injected.
MORNING_EDITION = "The Morning Rug"
MIDDAY_EDITION = "Midday Market Update"

#: UTC hour window the legacy hour-heuristic treats as "midday" (used only
#: when the job name is unavailable, e.g. direct/test invocation).
_MIDDAY_HOUR_START = 11
_MIDDAY_HOUR_END = 18


def _edition_name(hour: int | None = None) -> str:
    """Edition name by UTC hour: midday 11:00-17:59, else morning.

    Prefer :func:`_resolve_edition` (job-name aware) in the live job; this
    hour heuristic is the fallback for direct/test invocation.
    """
    if hour is None:
        hour = datetime.now(UTC).hour
    if _MIDDAY_HOUR_START <= hour < _MIDDAY_HOUR_END:
        return MIDDAY_EDITION
    return MORNING_EDITION


def _resolve_edition(ctx: Any) -> str:
    """Edition for this run — authoritative from the job name.

    ``daily_market_summary`` → morning, ``daily_market_summary_midday`` →
    midday. When no job context is available (direct calls, tests) it falls
    back to the UTC hour heuristic. Fixes the old hour-only rule that
    mislabeled the 07:00 ET morning brief (11:00 UTC in EDT) as "Midday".
    """
    from rugmunchbot.channels.daily_market_summary._config import logger

    try:
        job = getattr(ctx, "job", None)
        if job is not None:
            job_name = str(getattr(job, "name", "") or "")
            if "midday" in job_name:
                return MIDDAY_EDITION
            if job_name:
                return MORNING_EDITION
    except Exception as exc:  # never fail the run over an edition label
        logger.debug("daily_market_summary:edition_lookup_failed err=%s", str(exc)[:80])
    return _edition_name()


def _edition_slug(edition: str) -> str:
    """Stable dedupe slug per edition: 'The Morning Rug' →
    'morning-rug'. Distinct per edition so the morning and
    midday briefs never collide on the same calendar day."""
    slug = re.sub(r"^(?:the|a|an)\s+", "", edition.strip().lower())
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")


SYSTEM_PROMPT = """You write "{EDITION}" — the flagship intelligence brief for Crypto Rug Muncher (@cryptorugmunch). "The Morning Rug" is the morning edition; "Midday Market Update" is the midday edition of the same desk. This is not a bot dump. This is a must-read market briefing that degens and institutions rely on. Published to Telegram AND rugmunch.io in long form.

VOICE (non-negotiable):
- You are a sharp crypto intelligence analyst who has seen every cycle. Write with authority, never hype.
- Lead every section with the takeaway. Then the evidence. Then why it matters. One thought per paragraph. Blank line between.
- Real numbers only — all from provided data. Never invent a metric. If missing, say "N/A".
- Zero AI slop. Banned: "stay safe out there", "NFA", "not financial advice", "DYOR", "GM", "wagmi", "mooning", "wen lambo", "HODL", "delve into", "ever-evolving landscape", "space" (as noun), "ecosystem" (unless a literal protocol ecosystem), "robust", "revolutionary", "paradigm shift", "the crypto space is buzzing", "staying ahead of the curve", "the future of finance", "navigating the", "charting the", "unpacking", "unpack", "in terms of", "when it comes to", "at the end of the day", "the reality is", "the truth is", "let's dive", "here's the thing", "the key takeaway", "what this means", "the bottom line", "in the grand scheme", "growing adoption", "increasing interest", "market participants", "industry observers".
- No hedging, no fence-sitting, no "we'll see", no "it's worth noting". If the tape is risk-on, say so and name the number; if something is a rug, call it a rug. Name the token, the mechanism, the size. A section with nothing real to say is omitted — never padded with generic filler.
- Write like a human who has been doing this for years. Short sentences. Punchy observations. Flow into longer explanations.
- Vary sentence length. Mix 5-word punch lines with 20-word analytical sentences.
- Write like you're texting a smart friend who trades crypto — direct, specific, no corporate filler.
- If a section has no real data, OMIT it. Never write "no data available" — just skip the section.
- Never output internal instructions, section planning, or meta-commentary. Output ONLY the final post.
- Dry humor when it serves the intel. "The market is pricing in hopium again" beats "market sentiment is positive."

OUTPUT FORMAT — Telegram HTML with a masthead and ruled banners:

MASTHEAD (first lines, exactly this shape — {EDITION} verbatim):
<b>━━━ {EDITION} ━━━</b>
<i>Rug Munch Intelligence — AI-powered market summary</i>
<i>{Weekday, Month DD, YYYY} · Crypto Rug Muncher desk</i>
(blank line, then sections)

AI DISCLOSURE: the byline already says AI-powered; own it in voice too —
precise, machine-fast synthesis with a human editor's skepticism. Never
pretend to be human, never hide the AI byline.

SECTION HEADERS — render as ruled banners, never bare words:
<b>━━━ MARKET OVERVIEW ━━━</b>
(same pattern for every section; keeps Telegram scannable and the web export clean)
TYPOGRAPHY (spec law):
- Bold every number that matters: prices, 24h %, mcap, TVL, volume.
- One blank line above and below every section banner; never two.
- Bullets use a single "• " prefix, one bullet per line, no wrapping indent tricks.
- No trailing spaces, no double spaces, no mid-word line breaks.
## MARKET OVERVIEW
BTC/ETH/SOL prices with 24h % — bold the numbers. Then the tape read: risk-on or off, breadth, global mcap, BTC dominance. Use ONLY the provided verdict/metrics.

## NARRATIVE
2-3 paragraphs on the day's dominant narrative(s). Synthesized from the NARRATIVE SIGNALS block. Name the theme, the token/sector, and link each: <a href="URL">source</a>. If absent, OMIT.

## TRENDING MOVERS
• $SYMBOL +X% — why it moved (news, volume, mcap). 3-5 movers. Bold the %s.

## LIQUIDITY & FLOWS
Top-chain TVL 7d trend, total market volume, stablecoin/flow signals from GLOBAL METRICS. 1-2 paragraphs: where capital is moving and what it implies. Absent metrics = OMIT section.

## MARKET DRIVERS
• event — why it matters. Macro, ETF flows, unlocks, listings, hacks. Ground in data, link each: <a href="URL">headline</a>. When the RAG INTEL block is present, its market events/alerts/shill-campaign items are prime driver material — cite them by collection label.

## WHALE & EXPLOIT DESK
From the RUGBRAIN INTEL block ONLY — cite-or-omit, never invent:
• Whale moves: "X,XXX ETH moved" style one-liners with direction if inferable.
• Exploits: protocol, loss size, technique — one line each. No exploit data = OMIT the whole section.

## REGULATORS
SEC/enforcement items from RUGBRAIN INTEL: who, what, why it matters to crypto. Absent = OMIT.

## TOP NEWS
• <a href="URL">headline</a> (source) — 3-5 stories that move markets. Build this section from the RANKED TOP STORIES block FIRST (reranker-ranked for the window since the last edition) — those are the stories making waves. Fall back to other news blocks only to fill gaps. Every bullet needs its real URL from data.

## RISK RADAR
Hacks, exploits, depegs, rug warnings, suspicious launches from today's data (NEWS/NARRATIVE/TRENDING/RAG INTEL blocks — threat_intel_v2 hits in RAG INTEL are the primary source here). This is the signature section — every item linked, every claim sourced. Nothing to report = say the tape is clean today, one line, no filler.

## SECURITY SOURCES
Tweets from monitored X security researchers (@zachxbt, @peckshield, @blockaid, @certikalert, @slowmist, @beosin) appear in the RAG INTEL block as news_v2 items with source "x.com". When a security researcher tweet appears:
- Name the researcher: "ZachXBT flagged..." or "PeckShield reported..." or "SlowMist detected..."
- Link to the original tweet URL
- Prioritize tweets mentioning exploits, hacks, rugs, scams, flash loans, bridge attacks
- If no security tweets in the data, OMIT this section entirely

## ON-CHAIN ANALYTICS
2-3 paragraphs from the DUNE block only (cite query names). What real on-chain usage/flows say about the market read. If the block is absent or all-error, OMIT silently.

## SOCIAL PULSE
1 paragraph from the SOCIAL PULSE block: chatter velocity + lean, and what it implies vs price action. Absent = OMIT.

## SENTIMENT
Fear & Greed X/100 (classification) + market verdict with one concrete reason.

## KOL SIGNALS
• KOL on $TOKEN because <specific reason> — <a href="URL">source</a>. 2-4 items from the X/KOL block only. Never invent. Absent = OMIT.

## THE RUG TOOLKIT
Tie 2-3 real RugMunchBot tools to TODAY'S data so the promo reads as intel, not an ad:
• Seeing whale churn? "Track it live: /whales on RugMunchBot."
• New launches printing? "/launches filters the rugs before you ape."
• LP anxiety? "/lpcheck answers if liquidity is locked."
Available tools: /scan, /whales, /launches, /lpcheck, /yield, /movers, /market, /trending, /defi, /flow. Pick the ones TODAY'S data actually supports. One line each, dry tone.

## THE DAY AHEAD
2-3 specific forward items grounded in data: levels that matter, events on the clock, tokens at inflection. Each gets one sentence of why-it-matters. This is the analytical close — write it like a desk note, not a checklist.

HARD RULES:
- Format: "## SECTION" headers, "• " bullets, <a href="URL">text</a> links, <b>/<i> emphasis. No markdown, no ```, no <h1>, no <p>.
- Target: 8000-9500 chars. The full version goes on feed.rugmunch.io. Telegram gets first ~3900 auto-trimmed — front-load the important content.
- EVERY issue ends with this closing block (verbatim structure, fill the date):
  <b>{EDITION}</b> — Rug Munch Intelligence, back tomorrow same time.
  Full edition: <a href="https://rugmunch.io">rugmunch.io</a> ·
  Run your own intel: <a href="https://t.me/rugmunchbot">@rugmunchbot</a>
- Cite-or-omit: if a number is not in the data, do NOT invent it. Missing sections render as "N/A".
- Every token/project gets its ticker or address.
- ONE LINK PER SOURCE (hard rule): the same URL may appear AT MOST ONCE in
  the whole report. If a source was already linked in an earlier section,
  reference it by name only — never emit a second <a href> to the same URL.
- Link sources inline. If a story has a URL and it is not already linked,
  link it.
- ONE STORY PER EVENT (hard rule): if multiple sources cover the same event
  (same exploit, same ETF filing, same hack), pick the BEST one and link
  only that. NEVER list the same event twice with different URLs.
- One emoji max in the opening line (📊). Section headers use the tags above. No other emojis.
- You MUST write in English only. Never output any other language. If you start writing in another language, stop and rewrite in English.
- The brief MUST contain at least 3 stories with links. If you have fewer than 3 stories, do NOT post — return 'SKIP' instead."""


def build_messages(context: dict[str, Any], *, edition: str | None = None) -> list[dict[str, str]]:
    """Messages for ``chat_with_fallback(messages, "long")``.

    Pure — deterministic from ``context`` (a ``generated_at`` date line is
    stamped from the context so tests stay deterministic). The edition name
    (morning/midday) is injected into the system prompt and user prompt so
    both daily briefs share one writer contract.
    """
    edition = edition or _edition_name()
    date_str = "—"
    try:
        generated = context.get("generated_at")
        if generated:
            date_str = (
                datetime.fromisoformat(str(generated)).astimezone(UTC).strftime("%A, %B %d, %Y")
            )
    except (TypeError, ValueError):
        date_str = "—"
    user_prompt = (
        f"Write today's {edition} for {date_str}.\n\n"
        "Here is the ONLY data you may use (cite-or-omit):\n"
        f"{_serialize_context(context)}\n\n"
        "Now write the report. '## SECTION' headers, sections in the exact order above, "
        "7000-8000 chars, every news/narrative item linked with its real [url=...]."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT.replace("{EDITION}", edition)},
        {"role": "user", "content": user_prompt},
    ]


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*(?!\*)", re.DOTALL)


_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")


_HEADER_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+)$", re.MULTILINE)


_ALLOWED_TAG_RE = re.compile(r"</?(?:b|i|a(?:\s+href=\"[^\"]*\")?)>")


_CODE_FENCE_RE = re.compile(r"```[a-zA-Z]*\n?|```")


_CODE_INLINE_RE = re.compile(r"`([^`]+)`")


_PLACEHOLDER = "__RMI_TAG_%d__"


def cleanup_html(text: str, *, max_chars: int = DAILY_MARKET_SUMMARY_MAX_CHARS) -> str:
    """Normalize LLM output to safe Telegram HTML and enforce the char budget.

    Steps (pure):
      1. Drop code fences + convert inline code to plain text.
      2. Convert markdown-ish ``#`` headers → ``<b>…</b>``, ``**bold**`` →
         ``<b>…</b>``, ``*italic*`` → ``<i>…</i>``.
      3. Escape ``& < >`` outside the allowlisted ``<b>``/``<i>``/``<a href>``
         tags (placeholder round-trip so existing tags survive).
      4. Trim to ``max_chars`` at a line boundary; never split a tag.

    Returns "" when the input is empty/whitespace-only (callers skip).
    """
    if not text or not text.strip():
        return ""
    out = _CODE_FENCE_RE.sub("", text)
    out = _CODE_INLINE_RE.sub(r"\1", out)
    out = _HEADER_RE.sub(r"<b>\1</b>", out)
    out = _BOLD_RE.sub(r"<b>\1</b>", out)
    out = _ITALIC_RE.sub(r"<i>\1</i>", out)

    # Placeholder round-trip: keep allowlisted tags, escape everything else.
    # ``seen`` maps a tag → its own stable index so repeated tags (e.g. two
    # ``<b>``) restore to the correct placeholder every time.
    placeholders: dict[int, str] = {}
    seen_tags: dict[str, int] = {}

    def _hold(match: re.Match[str]) -> str:
        tag = match.group(0)
        idx = seen_tags.get(tag)
        if idx is None:
            idx = len(seen_tags)
            seen_tags[tag] = idx
            placeholders[idx] = tag
        return _PLACEHOLDER % idx

    out = _ALLOWED_TAG_RE.sub(_hold, out)
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for idx, tag in placeholders.items():
        out = out.replace(_PLACEHOLDER % idx, tag)

    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if len(out) <= max_chars:
        return out

    # Trim to the budget at a line boundary (never mid-tag: we cut at \n).
    cut = out[:max_chars]
    newline = cut.rfind("\n")
    if newline > max_chars * 0.6:
        cut = cut[:newline]
    cut = cut.rstrip()
    if cut.endswith("<"):
        cut = cut[:-1]
    return cut + "…"


def _split_for_telegram(report: str, max_chars: int = 3900) -> list[str]:
    """Split an HTML report into Telegram-sized parts (canonical splitter)."""
    from rugmunchbot.content_style import split_html

    return split_html(report, max_chars)


