from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

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


async def publish_to_feed(
    report: str,
    *,
    title: str | None = None,
    edition: str | None = None,
    feature_image: str = "",
) -> dict[str, Any]:
    """Publish the brief to the rugmunch.io feed (Ghost content platform).

    The rugmunch.io social feed is backed by the Ghost content platform
    (``app.content_platform.py`` — "@CryptoRugMunch Feed engine"); the same
    ``create_post`` path the Morning Brief uses (``post_type="briefing"``).
    ``feature_image`` is the resolved article image; when empty ``create_post``
    falls back to the branded placeholder so the feed post always carries one.
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
            feature_image=feature_image,
        )
    except Exception as exc:  # fail-open — feed publish is additive
        logger.warning("daily_market_summary:feed_publish_failed err=%s", str(exc)[:200])
        return {"error": str(exc)}


#: Fingerprints of echoed PROMPT-SPEC text (instruction language), checked
#: case-insensitively in the ~240 chars after a guarded marker. Legit
#: section headers are followed by real data (numbers, tickers, links) and
#: never contain these; an echoed spec block always does (nearly every spec
#: section ends with "Absent = OMIT" or similar instruction language).
_SPEC_ECHO_HINTS: Final[tuple[str, ...]] = (
    "omit",
    "absent",
    "cite-or-omit",
    "bullets",
    "paragraphs",
    "bold the",
    "build this section",
    "reranker",
    "fear & greed",
    "desk note",
    "cite them by",
    "one emoji max",
    "target:",
    "hard rules",
    "one link per source",
    "one story per event",
    "every token/project",
    "reads as intel",
    "tie 1-2",
    "tie 2-3",
    "cite query names",
    "chatter velocity",
    "why it matters to crypto",
)


def _strip_prompt_marker(raw: str, marker: str, *, guarded: bool = False) -> str:
    """Truncate *raw* at the first occurrence of *marker* (prompt echo).

    Guarded markers (``## SECTION``-style and ``MARKET DRIVERS``) are ALSO
    legitimate output shapes — the prompts mandate ``## SECTION`` headers
    and the summary has a real ``## MARKET DRIVERS`` section — so they only
    truncate when the following text carries a spec-echo fingerprint. Plain
    markers (``MASTHEAD DONE``, ``AI DISCLOSURE:``, raw feed slugs) are
    never legitimate output and always truncate.
    """
    idx = raw.find(marker)
    if idx < 0:
        return raw
    if guarded:
        window = raw[idx : idx + 240].lower()
        if not any(hint in window.lower() for hint in _SPEC_ECHO_HINTS):
            return raw  # legit section header with real content — keep it
    return raw[:idx].rstrip()


#: Common non-English (Spanish/French/German) stopwords used for the cheap
#: language gate. A brief whose word share of these exceeds the threshold is
#: treated as a language leak and skipped rather than shipped to subscribers.
_NON_ENGLISH_WORDS = frozenset(
    {
        # Spanish
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "y",
        "o",
        "u",
        "de",
        "del",
        "en",
        "con",
        "por",
        "para",
        "que",
        "es",
        "son",
        "se",
        "informe",
        "viernes",
        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "sábado",
        "domingo",
        "ha",
        "han",
        "he",
        "hemos",
        "está",
        "están",
        "este",
        "esta",
        "esto",
        "estos",
        "estas",
        "su",
        "sus",
        "nuestro",
        "nuestra",
        "también",
        "pero",
        "más",
        "menos",
        "como",
        "cuando",
        "donde",
        "porque",
        "ya",
        "hoy",
        "ayer",
        "mañana",
        "después",
        "antes",
        "sobre",
        "entre",
        "hacia",
        "desde",
        "hasta",
        "sin",
        "muy",
        "bien",
        "mal",
        "todo",
        "toda",
        "todos",
        "todas",
        "nuevo",
        "nueva",
        "nuevos",
        "nuevas",
        "mercado",
        "precio",
        "precios",
        "token",
        "tokens",
        "cripto",
        "criptomonedas",
        "generado",
        "cumpliendo",
        "filler",
        "colores",
        "masthead",
        "forma",
        "resumen",
        "análisis",
        "analisis",
        "noticias",
        "semana",
        "semanal",
        "diario",
        "diaria",
        "usuario",
        "usuarios",
        "preguntó",
        "pregunto",
        "escribir",
        "escribe",
        "escribiré",
        "voy",
        "vamos",
        "necesito",
        "necesita",
        "debo",
        "debe",
        "puedo",
        "puede",
        "quiero",
        "quiere",
        "hacer",
        "hago",
        "haré",
        "ahora",
        "luego",
        # French
        "le",
        "les",
        "une",
        "des",
        "et",
        "ou",
        "du",
        "avec",
        "pour",
        "par",
        "qui",
        "est",
        "sont",
        "ce",
        "cet",
        "cette",
        "ces",
        "sa",
        "ses",
        "notre",
        "nos",
        "aussi",
        "mais",
        "plus",
        "moins",
        "comme",
        "quand",
        "où",
        "parce",
        "déjà",
        "aujourd'hui",
        "hier",
        "demain",
        "après",
        "avant",
        "sur",
        "vers",
        "depuis",
        "jusqu'à",
        "sans",
        "très",
        "tout",
        "toute",
        "tous",
        "toutes",
        "nouveau",
        "nouvelle",
        "marché",
        "prix",
        "crypto",
        "cryptomonnaies",
        "généré",
        "respectant",
        "remplissage",
        "couleurs",
        "en-tête",
        "forme",
        "rapport",
        "résumé",
        "analyse",
        "actualités",
        "semaine",
        "hebdomadaire",
        "quotidien",
        "utilisateur",
        "utilisateurs",
        "demandé",
        "écrire",
        "écris",
        "écrirai",
        "vais",
        "allons",
        "besoin",
        "dois",
        "doit",
        "peux",
        "peut",
        "veux",
        "veut",
        "faire",
        "fais",
        "ferai",
        "maintenant",
        "ensuite",
        # German
        "der",
        "die",
        "das",
        "ein",
        "eine",
        "einer",
        "eines",
        "und",
        "oder",
        "von",
        "vom",
        "im",
        "am",
        "mit",
        "für",
        "durch",
        "dass",
        "ist",
        "sind",
        "war",
        "waren",
        "sein",
        "seine",
        "seiner",
        "unser",
        "unsere",
        "auch",
        "aber",
        "mehr",
        "weniger",
        "wie",
        "wann",
        "wo",
        "weil",
        "schon",
        "heute",
        "gestern",
        "morgen",
        "nach",
        "vor",
        "über",
        "zwischen",
        "gegen",
        "seit",
        "bis",
        "ohne",
        "sehr",
        "gut",
        "alles",
        "alle",
        "neue",
        "neuer",
        "neues",
        "markt",
        "preis",
        "preise",
        "krypto",
        "kryptowährungen",
        "generiert",
        "erfüllt",
        "füller",
        "farben",
        "kopfzeile",
        "form",
        "bericht",
        "zusammenfassung",
        "nachrichten",
        "woche",
        "wöchentlich",
        "täglich",
        "benutzer",
        "gefragt",
        "schreiben",
        "schreibe",
        "werde",
        "werden",
        "brauche",
        "muss",
        "müssen",
        "kann",
        "können",
        "will",
        "wollen",
        "machen",
        "mache",
        "jetzt",
        "dann",
    }
)

#: Max share of non-English words before a brief is treated as a language leak.
_LANGUAGE_LEAK_THRESHOLD = 0.20


def _is_language_leak(text: str) -> bool:
    """True when >20% of the words are common Spanish/French/German stopwords.

    Cheap deterministic guard against the LLM drifting into another language.
    HTML tags/attrs are stripped first so markup doesn't skew the word count.
    """
    plain = re.sub(r"<[^>]+>", " ", text)
    words = re.findall(r"[A-Za-zÀ-ÿ']+", plain.lower())
    if not words:
        return False
    non_english = sum(1 for w in words if w in _NON_ENGLISH_WORDS)
    return (non_english / len(words)) > _LANGUAGE_LEAK_THRESHOLD


def _has_min_stories(text: str, *, min_links: int = 3) -> bool:
    """True when the brief carries at least ``min_links`` distinct http links.

    Guards against the LLM emitting a brief with no real stories (empty
    content / single-link posts).
    """
    links = set(re.findall(r"https?://[^\s)>\"']+", text))
    return len(links) >= min_links


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
    for marker in (
        "AI DISCLOSURE:",
        "OUTPUT FORMAT",
        "MASTHEAD (",
        "MASTHEAD DONE",
        "NOW THE BODY",
        "MARKET DRIVERS",
        "cryptonews_rss",
        "rag_intel",
        "ranked_stories",
        "HARD RULES",
        "SECTION HEADERS",
        "TYPOGRAPHY",
        "## MARKET",
        "## NARRATIVE",
        "## TRENDING",
        "## LIQUIDITY",
        "## WHALE",
        "## REGULATORS",
        "## TOP NEWS",
        "## RISK",
        "## ON-CHAIN",
        "## SOCIAL",
        "## SENTIMENT",
        "## KOL",
        "## THE RUG TOOLKIT",
        "El informe",
        "The user just asked",
        "No further action needed",
        "ha sido generado",
        "cumpliendo todas",
        "Sin filler",
        "Colores y",
        "Masthead con la forma",
    ):
        raw = _strip_prompt_marker(
            raw, marker, guarded=marker.startswith("## ") or marker == "MARKET DRIVERS"
        )
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

    # Post-generation guard (2026-09-04): never ship an empty brief or a
    # language leak. A report with <100 words or fewer than 3 distinct links
    # is an empty/placeholder draft — skip. A report whose word share of
    # common Spanish/French/German stopwords exceeds 20% is a language
    # drift — skip.
    word_count = len(re.findall(r"\S+", report))
    if word_count < 100:
        logger.warning("daily_market_summary:too_short_skipped words=%d min=100", word_count)
        return 0
    if not _has_min_stories(report):
        logger.warning(
            "daily_market_summary:too_few_stories_skipped links=%d min=3",
            len(set(re.findall(r"https?://[^\s)>\"']+", report))),
        )
        return 0
    if _is_language_leak(report):
        logger.warning("daily_market_summary:language_leak_skipped")
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
        feed_result = await publish_to_feed(
            raw,
            edition=edition,
            feature_image=gate.image_url or _pick_report_image(context),
        )
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
                ctx.bot, chat_id, _ImagePost(part, image_url), _safe_send, content_type="market"
            )
        else:
            sent_ok = await _safe_send(ctx.bot, chat_id, part, content_type="market")
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
    """Resolve the report image from the TOP STORY's article URL (og:image).

    2026-09-01 FINAL: story-doc ``image_url`` metadata is UNTRUSTED —
    existing Qdrant docs carry the operator's brand banner from the
    pre-fix era (it shipped on posts 7879/7883/7886). The report image
    resolves ONLY from the top story's own article URL via the thumb
    proxy; no image → "" → plain-text posting. Sync wrapper over the
    async resolver (the caller is sync context).
    """
    import asyncio

    from rugmunchbot.support.image_policy import resolve_post_image

    for item in context.get("news") or []:
        if not isinstance(item, dict):
            continue
        source_url = str(item.get("url") or item.get("source_url") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            continue
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                # Called from async context — schedule and wait
                resolved = asyncio.create_task(resolve_post_image("news", source_url=source_url))
                return resolved.result() if resolved.done() else ""
            resolved = asyncio.run(resolve_post_image("news", source_url=source_url))
            if resolved:
                return resolved
        except Exception as exc:
            logger.debug(
                "report_image_resolve_failed src=%s err=%s", source_url[:60], str(exc)[:80]
            )
            continue
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
