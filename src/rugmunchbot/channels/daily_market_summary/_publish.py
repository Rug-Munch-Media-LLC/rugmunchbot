from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.daily_market_summary._assemble import (
    _serialize_context,
    gather_summary_data,
)
from rugmunchbot.channels.daily_market_summary._config import (
    DAILY_MARKET_SUMMARY_FEED_ENABLED,
    DAILY_MARKET_SUMMARY_MAX_CHARS,
    logger,
)

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


SYSTEM_PROMPT = """You write "{EDITION}" — the flagship intelligence brief for Crypto Rug Muncher (@cryptorugmunch). "The Morning Rug" is the morning edition; "Midday Market Update" is the midday edition of the same desk. This is not a bot dump. This is a must-read market briefing that degens and institutions rely on. Published to Telegram AND rugmunch.io in long form.

VOICE (non-negotiable):
- You are a sharp crypto intelligence analyst who has seen every cycle. Write with authority, never hype.
- Lead every section with the takeaway. Then the evidence. Then why it matters. One thought per paragraph. Blank line between.
- Real numbers only — all from provided data. Never invent a metric. If missing, say "N/A".
- Zero AI slop. Banned: "stay safe out there", "NFA", "not financial advice", "DYOR", "GM", "wagmi", "mooning", "wen lambo", "HODL", "delve into", "ever-evolving landscape", "space" (as noun), "ecosystem" (unless a literal protocol ecosystem), "robust", "revolutionary", "paradigm shift", "the crypto space is buzzing", "staying ahead of the curve", "the future of finance".
- No hedging, no fence-sitting, no "we'll see", no "it's worth noting". If the tape is risk-on, say so and name the number; if something is a rug, call it a rug. Name the token, the mechanism, the size. A section with nothing real to say is omitted — never padded with generic filler.
- Write like a human who has been doing this for years. Short sentences. Punchy observations. Flow into longer explanations.
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
- One emoji max in the opening line (📊). Section headers use the tags above. No other emojis."""


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


async def publish_to_feed(
    report: str, *, title: str | None = None, edition: str | None = None
) -> dict[str, Any]:
    """Publish the brief to the rugmunch.io feed (Ghost content platform).

    The rugmunch.io social feed is backed by the Ghost content platform
    (``app.content_platform.py`` — "@CryptoRugMunch Feed engine"); the same
    ``create_post`` path the Morning Brief uses (``post_type="briefing"``).
    Fail-open: missing session cookie, network errors, and Ghost HTTP errors
    all log and return — the Telegram post is never blocked.
    """
    stamp = datetime.now(UTC).strftime("%B %d, %Y")
    try:
        from rugmunchbot._vendor.content_platform import create_post

        return await create_post(
            content=report,
            post_type="briefing",
            title=title or f"{edition or _edition_name()} — {stamp}",
            tags=["daily", "market-summary"],
        )
    except Exception as exc:  # fail-open — feed publish is additive
        logger.warning("daily_market_summary:feed_publish_failed err=%s", str(exc)[:200])
        return {"error": str(exc)}


async def post_daily_market_summary(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Gather → LLM-write → clean → post to @cryptorugmuncher + feed.

    Returns 1 when the Telegram post shipped, 0 otherwise. Fails open at every
    step: missing chat id, no data, LLM failure, Telegram send error, or feed
    publish error all log and return without raising.
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from rugmunchbot.channels.scheduler import _chat_id, _safe_send
    from rugmunchbot.channels.scheduler._core import _post_photo_aware

    chat_id = _chat_id("main")  # registry ``main`` → @cryptorugmuncher
    if not chat_id:
        logger.info("daily_market_summary:no_main_chat_id_skipped")
        return 0

    context = await gather_summary_data()
    if not context.get("sources"):
        logger.info("daily_market_summary:no_data_sources_skipped")
        return 0

    edition = _resolve_edition(ctx)
    messages = build_messages(context, edition=edition)
    try:
        from rugmunchbot.llm_config import chat_with_fallback

        raw = await chat_with_fallback(messages, "long")
    except Exception as exc:  # fail-open — a broken LLM never crashes the tick
        logger.warning("daily_market_summary:llm_failed err=%s", str(exc)[:160])
        raw = ""
    # Strip prompt scaffolding the model may echo verbatim (meta-instruction
    # headers like "AI DISCLOSURE:" are voice guidance, never output).
    for marker in ("AI DISCLOSURE:", "OUTPUT FORMAT", "MASTHEAD ("):
        if marker in raw:
            raw = raw.split(marker, 1)[0].rstrip()
    # Stub guard (2026-08-26): the morning run posted a 154-char stub
    # because the LLM chain returned a degenerate one-off. If the model
    # under-delivers, retry once with an explicit length demand; if it
    # STILL under-delivers, assemble a deterministic report from the raw
    # context — the market report never ships empty again.
    min_chars = 1500
    if len(raw.strip()) < min_chars:
        logger.warning("daily_market_summary:llm_short retrying chars=%s", len(raw.strip()))
        retry_msgs = build_messages(context, edition=edition)
        retry_msgs[1]["content"] += (
            "\n\nIMPORTANT: your previous attempt was too short. Write the FULL "
            "7000-8000 character report now — every section, every item, with links."
        )
        try:
            from rugmunchbot.llm_config import chat_with_fallback

            raw2 = await chat_with_fallback(retry_msgs, "long")
            if len(raw2.strip()) > len(raw.strip()):
                raw = raw2
        except Exception as exc:
            logger.warning("daily_market_summary:llm_retry_failed err=%s", str(exc)[:120])
    if not raw or not raw.strip():
        logger.info("daily_market_summary:llm_empty_skipped sources=%s", context.get("sources"))
        return 0

    report = cleanup_html(raw)
    if not report:
        logger.info("daily_market_summary:cleaned_empty_skipped")
        return 0

    # HARD quality gate (2026-08-31): the flagship report must pass the
    # deterministic content lint before it touches the channel. Autofix
    # (dedupe links, placeholder image) runs first; a still-dirty report
    # gets ONE stricter-prompt LLM regeneration; if it STILL fails the
    # edition is skipped — crap never ships to @cryptorugmuncher.
    from rugmunchbot.support.content_lint import canonical_url
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    async def _regenerate_report(bad_text: str, violations: list[Any]) -> str:
        del bad_text
        detail = "; ".join(f"{v.rule}: {v.phrase}" for v in violations[:6])
        retry_msgs = build_messages(context, edition=edition)
        retry_msgs[1]["content"] += (
            "\n\nSTRICT REWRITE REQUIRED — your previous draft failed the "
            f"content quality lint ({detail[:300]}). Rules: no banned phrases, "
            "each URL appears AT MOST ONCE, at least 20 words of original "
            "commentary per story, no '[SEV] Title / Source:' template shape. "
            "Rewrite the FULL report now."
        )
        from rugmunchbot.llm_config import chat_with_fallback

        return await chat_with_fallback(retry_msgs, "long")

    gate = await pre_post_quality_gate(
        report,
        image_url=_pick_report_image(context),
        source=edition,
        kind="market_summary",
        regenerate=_regenerate_report,
        logger=logger,
    )
    if not gate.ok:
        logger.warning(
            "daily_market_summary:quality_gate_blocked edition=%s violations=%d",
            edition,
            len(gate.violations),
        )
        return 0
    report = gate.text
    if gate.regenerated:
        report = cleanup_html(report) or report
    logger.info(
        "daily_market_summary:report_ready chars=%s sources=%s",
        len(report),
        context.get("sources"),
    )

    # Market-report delivery doctrine (2026-08-26): Telegram readers get the
    # FULL report — split at line boundaries into ≤3900-char parts (Telegram's
    # 4096 limit minus formatting headroom). No trimming: free-for-all long
    # form on TG; feed.rugmunch.io carries the most detailed edition.
    #
    # Feed publishes FIRST so the TG closing block can link the live article.
    feed_url = ""
    if DAILY_MARKET_SUMMARY_FEED_ENABLED:
        feed_result = await publish_to_feed(raw, edition=edition)
        feed_url = str(feed_result.get("url") or "") if isinstance(feed_result, dict) else ""
        if feed_url:
            logger.info("daily_market_summary:feed_published url=%s", feed_url)

    parts = _split_for_telegram(report)
    # One-link-per-source: only append the feed link when the report body
    # does not already link it (the LLM is instructed to link each source
    # once; the closer must never duplicate an existing link). Comparison is
    # canonical-URL (http/https, trailing slash, tracking params) so a
    # differently-formatted copy of the same link still counts as linked.
    already_linked = bool(feed_url) and canonical_url(feed_url) in {
        canonical_url(u) for u in re.findall(r"https?://[^\s)>\"']+", report)
    }
    closer = (
        f"📖 <b>{edition}</b> — full write-up on feed.rugmunch.io"
        + (f' — <a href="{feed_url}">read it here</a>.' if feed_url and not already_linked else ".")
        + "\n🤖 Live token intel, scans &amp; alerts: @rugmunchbot"
    )
    parts[-1] = parts[-1].rstrip() + "\n\n" + closer

    # Photo-aware delivery (2026-09): the flagship report leads with an image
    # when the gathered context carries one (top news story's og:image). The
    # first part goes through the shared photo-aware path — a caption-too-long
    # or bad-URL failure falls back to plain text inside send_news_photo, so
    # delivery is never blocked by the image.
    image_url = gate.image_url or _pick_report_image(context)

    sent = 0
    for idx, part in enumerate(parts, 1):
        if idx == 1 and image_url:
            sent_ok = await _post_photo_aware(
                ctx.bot, chat_id, _ImagePost(part, image_url), _safe_send
            )
        else:
            sent_ok = await _safe_send(ctx.bot, chat_id, part)
        if sent_ok:
            sent += 1
            logger.info(
                "daily_market_summary:posted_part %d/%d chars=%s",
                idx,
                len(parts),
                len(part),
            )
        else:
            break
    if sent == 0:
        return 0
    # W30 top-of-food-chain mirror: a main-channel post lands on
    # feed.rugmunch.io too (best-effort, never blocks the tick). The Ghost
    # publish above covers rugmunch.io; this covers the static feed store.
    try:
        from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

        mirror_post_to_feed(
            {
                "text": report[:3900],
                "story_ref": feed_url
                or f"{_edition_slug(edition)}-{datetime.now(UTC).strftime('%Y%m%d')}",
                "source": feed_url or "rugmunch.io",
                "kind": "market-summary",
                "image_url": gate.image_url or _pick_report_image(context),
            }
        )
    except Exception as exc:
        logger.warning("daily_market_summary:feed_mirror_failed err=%s", str(exc)[:120])
    return 1


class _ImagePost(str):
    """A report part carrying an ``image_url`` — duck-types ``NewsPost`` so
    the shared ``_post_photo_aware`` sender attaches the photo."""

    image_url: str = ""

    def __new__(cls, text: str, image_url: str) -> _ImagePost:
        obj = super().__new__(cls, text)
        obj.image_url = image_url
        return obj


def _pick_report_image(context: dict[str, Any]) -> str:
    """First usable http(s) story image from the gathered context (or "").

    Reads the RAG news hits (``context["news"]`` — each carries ``image_url``
    from stored metadata). Fail-open: no image → "" → plain-text posting.
    """
    for item in context.get("news") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("image_url") or "").strip()
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _edition_slug(edition: str) -> str:
    """Stable dedupe slug per edition: 'The Morning Rug' →
    'morning-rug'. Distinct per edition so the morning and
    midday briefs never collide on the same calendar day."""
    slug = re.sub(r"^(?:the|a|an)\s+", "", edition.strip().lower())
    return re.sub(r"[^a-z0-9]+", "-", slug).strip("-")


def _split_for_telegram(report: str, max_chars: int = 3900) -> list[str]:
    """Split an HTML report into Telegram-sized parts at line boundaries.

    Never cuts mid-tag or mid-line; keeps part order so the report reads
    continuously across messages. Returns [report] when it already fits.
    """
    if len(report) <= max_chars:
        return [report]
    parts: list[str] = []
    remaining = report
    while len(remaining) > max_chars:
        cut = remaining[:max_chars]
        newline = cut.rfind("\n")
        if newline > max_chars * 0.5:
            cut = cut[:newline]
        cut = cut.rstrip()
        if cut.endswith("<"):
            cut = cut[:-1]
        parts.append(cut)
        remaining = remaining[len(cut) :].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


if TYPE_CHECKING:
    from telegram.ext import ContextTypes
