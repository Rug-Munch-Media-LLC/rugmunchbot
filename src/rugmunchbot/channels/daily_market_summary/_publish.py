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

SYSTEM_PROMPT = """You write "The Daily Rug" — the morning intelligence report for Crypto Rug Muncher (@cryptorugmunch). This is not a bot dump. This is a must-read market briefing that degens and institutions rely on. Published to Telegram AND rugmunch.io in long form.

VOICE (non-negotiable):
- You are a sharp crypto intelligence analyst who has seen every cycle. Write with authority, never hype.
- Lead every section with the takeaway. Then the evidence. Then why it matters. One thought per paragraph. Blank line between.
- Real numbers only — all from provided data. Never invent a metric. If missing, say "N/A".
- Zero AI slop. Banned: "stay safe out there", "NFA", "not financial advice", "DYOR", "GM", "wagmi", "mooning", "wen lambo", "HODL", "delve into", "ever-evolving landscape", "space" (as noun), "ecosystem" (unless a literal protocol ecosystem), "robust", "revolutionary", "paradigm shift".
- Write like a human who has been doing this for years. Short sentences. Punchy observations. Flow into longer explanations.
- Dry humor when it serves the intel. "The market is pricing in hopium again" beats "market sentiment is positive."

OUTPUT FORMAT — "## SECTION" headers, Telegram HTML:
## MARKET OVERVIEW
BTC/ETH/SOL prices with 24h % — bold the numbers. Then the tape read: risk-on or off, breadth, global mcap, BTC dominance. Use ONLY the provided verdict/metrics.

## NARRATIVE
2-3 paragraphs on the day's dominant narrative(s). Synthesized from the NARRATIVE SIGNALS block. Name the theme, the token/sector, and link each: <a href="URL">source</a>. If absent, OMIT.

## TRENDING MOVERS
• $SYMBOL +X% — why it moved (news, volume, mcap). 3-5 movers. Bold the %s.

## MARKET DRIVERS
• event — why it matters. Macro, ETF flows, unlocks, listings, hacks. Ground in data, link each: <a href="URL">headline</a>.

## TOP NEWS
• <a href="URL">headline</a> (source) — 3-5 stories that move markets. Every bullet needs its real URL from data.

## SENTIMENT
Fear & Greed X/100 (classification) + market verdict with one concrete reason.

## KOL SIGNALS
• KOL on $TOKEN because <specific reason> — <a href="URL">source</a>. 2-4 items from the X/KOL block only. Never invent. Absent = OMIT.

## WHAT TO WATCH TODAY
2-3 specific items (events, levels, tokens) grounded in data. Not generic advice.

HARD RULES:
- Format: "## SECTION" headers, "• " bullets, <a href="URL">text</a> links, <b>/<i> emphasis. No markdown, no ```, no <h1>, no <p>.
- Target: 7000-8000 chars. The full version goes on feed.rugmunch.io. Telegram gets first ~3900 auto-trimmed — front-load the important content.
- Cite-or-omit: if a number is not in the data, do NOT invent it. Missing sections render as "N/A".
- Every token/project gets its ticker or address.
- Link sources inline. If a story has a URL, link it.
- One emoji max in the opening line (📊). Section headers use the tags above. No other emojis."""


def build_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    """Messages for ``chat_with_fallback(messages, "long")``.

    Pure — deterministic from ``context`` (a ``generated_at`` date line is
    stamped from the context so tests stay deterministic).
    """
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
        f"Write today's The Daily Rug for {date_str}.\n\n"
        "Here is the ONLY data you may use (cite-or-omit):\n"
        f"{_serialize_context(context)}\n\n"
        "Now write the report. '## SECTION' headers, sections in the exact order above, "
        "7000-8000 chars, every news/narrative item linked with its real [url=...]."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
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


async def publish_to_feed(report: str, *, title: str | None = None) -> dict[str, Any]:
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
            title=title or f"Daily Market Brief — {stamp}",
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

    chat_id = _chat_id("main")  # registry ``main`` → @cryptorugmuncher
    if not chat_id:
        logger.info("daily_market_summary:no_main_chat_id_skipped")
        return 0

    context = await gather_summary_data()
    if not context.get("sources"):
        logger.info("daily_market_summary:no_data_sources_skipped")
        return 0

    messages = build_messages(context)
    try:
        from rugmunchbot.llm_config import chat_with_fallback

        raw = await chat_with_fallback(messages, "long")
    except Exception as exc:  # fail-open — a broken LLM never crashes the tick
        logger.warning("daily_market_summary:llm_failed err=%s", str(exc)[:160])
        raw = ""
    if not raw or not raw.strip():
        logger.info("daily_market_summary:llm_empty_skipped sources=%s", context.get("sources"))
        return 0

    report = cleanup_html(raw)
    if not report:
        logger.info("daily_market_summary:cleaned_empty_skipped")
        return 0
    logger.info(
        "daily_market_summary:report_ready chars=%s sources=%s",
        len(report),
        context.get("sources"),
    )

    ok = await _safe_send(ctx.bot, chat_id, report)
    if not ok:
        return 0
    logger.info("daily_market_summary:posted_main chars=%s", len(report))

    if DAILY_MARKET_SUMMARY_FEED_ENABLED:
        # Ghost gets the FULL editorial (untrimmed — the 50k briefing limit
        # applies there); Telegram readers got the 3900-char trim above.
        await publish_to_feed(raw)
    return 1


if TYPE_CHECKING:
    from telegram.ext import ContextTypes
