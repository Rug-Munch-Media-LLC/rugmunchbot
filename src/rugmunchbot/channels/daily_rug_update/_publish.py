"""Daily rug update — LLM prompt, quality gate, feed mirror, posting.

Pipeline (``post_daily_rug_update``): gather → LLM-write (cryptorugmuncher
voice) → deterministic quality gate (``support.content_lint``) → Ghost feed
publish → static feed-store mirror (``mirror_post_to_feed``) → Telegram post
with image + a link to the exact post on rugmunch.io
(``feed.rugmunch.io/p/{slug}``). Fails open at every step — a broken lane
logs and the run no-ops; the Telegram post is never blocked by the feed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.channels.daily_rug_update._config import (
    DAILY_RUG_UPDATE_FEED_ENABLED,
    DAILY_RUG_UPDATE_MAX_CHARS,
)
from rugmunchbot.channels.daily_rug_update._fetch import (
    _serialize_context,
    gather_rug_update_data,
    pick_update_image,
)

logger = logging.getLogger("rmi_bot.channels.daily_rug_update")

EDITION = "Midday Market Update"

#: feed.rugmunch.io deep-link base for a mirrored post slug.
FEED_POST_URL_FMT = "https://feed.rugmunch.io/p/{slug}"

SYSTEM_PROMPT = """You write "{EDITION}" — the midday market update for Crypto Rug Muncher (@cryptorugmuncher). It leads with the highest-ranked stories from the reranker over the window since the last Morning Rug, then layers the rug/scam intelligence desk on top. Published to Telegram AND rugmunch.io in long form.

VOICE (non-negotiable):
- You are a sharp crypto intelligence analyst who has seen every cycle. Write with authority, never hype.
- Lead every section with the takeaway. Then the evidence. Then why it matters. One thought per paragraph. Blank line between.
- Real numbers only — all from provided data. Never invent a metric. If missing, say "N/A".
- Zero AI slop. Banned: "stay safe out there", "NFA", "not financial advice", "DYOR", "GM", "wagmi", "mooning", "wen lambo", "HODL", "delve into", "ever-evolving landscape", "robust", "revolutionary", "paradigm shift", "staying ahead of the curve", "the future of finance", "it's worth noting", "stay tuned".
- No hedging, no fence-sitting. If something is a rug, call it a rug. Name the token, the mechanism, the size. A section with nothing real to say is omitted — never padded with generic filler.
- Dry humor when it serves the intel. "The market is pricing in hopium again" beats "market sentiment is positive."

OUTPUT FORMAT — Telegram HTML with a masthead and ruled banners:

MASTHEAD (first lines, exactly this shape — {EDITION} verbatim):
<b>━━━ {EDITION} ━━━</b>
<i>Rug Munch Intelligence — AI-powered rug & scam summary</i>
<i>{Weekday, Month DD, YYYY} · Crypto Rug Muncher desk</i>
(blank line, then sections)

SECTION HEADERS — render as ruled banners:
<b>━━━ SECTION NAME ━━━</b>

## TOP RUGS & SCAMS
The biggest rugs/scams detected in the last 24h from the SCANNER ALERTS block. For each: token + chain, the mechanism (honeypot, mint abuse, LP pull, etc.), the risk score, and why it matters. Link each source once: <a href="URL">source</a>. Bold the numbers. Absent = OMIT.

## SHILL CAMPAIGNS
Coordinated pushes from the SHILL CAMPAIGNS block: token, how many KOLs, how many posts, over what span. Real analysis of the pattern — coordination this tight is usually an exit ramp, say so. Absent = OMIT.

## KOLS CAUGHT
KOLs caught pushing scam tokens, from the shill-campaign handles and RAG INTEL shill_evidence items. Name the handles, name the token, state the evidence count. Never invent a handle. Absent = OMIT.

## WHALE MOVES
Notable whale movements from the RAG INTEL block only — cite-or-omit, never invent. "X,XXX ETH moved" style one-liners with direction if inferable. Absent = OMIT.

## BREAKING NEWS
The stories making waves right now, from the RANKED TOP STORIES block FIRST (reranker-ranked over the window since the last Morning Rug — highest-ranked first) and the RAG INTEL news_v2 hits to fill gaps. Every item linked with its real URL. Absent = OMIT.

## THE RUG TOOLKIT
Tie 1-2 real RugMunchBot tools to TODAY'S data so the promo reads as intel, not an ad: "/scan answers if a token is a honeypot before you ape." Available: /scan, /whales, /launches, /lpcheck. One line each, dry tone.

HARD RULES:
- Format: "## SECTION" headers, "• " bullets, <a href="URL">text</a> links, <b>/<i> emphasis. No markdown, no ```, no <h1>, no <p>.
- Target: 2500-3500 chars. Front-load the important content.
- Cite-or-omit: if a fact is not in the data, do NOT invent it.
- Every token/project gets its ticker or address.
- ONE LINK PER SOURCE (hard rule): the same URL may appear AT MOST ONCE in the whole post. If a source was already linked, reference it by name only.
- One emoji max in the opening line ( rugs). Section headers use the tags above. No other emojis."""


def build_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    """Messages for ``chat_with_fallback(messages, "long")``. Pure."""
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
        f"Write today's {EDITION} for {date_str}.\n\n"
        "Here is the ONLY data you may use (cite-or-omit):\n"
        f"{_serialize_context(context)}\n\n"
        "Now write the update. '## SECTION' headers, sections in the exact order above, "
        "2500-3500 chars, every item linked with its real [url=...] at most once."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT.replace("{EDITION}", EDITION)},
        {"role": "user", "content": user_prompt},
    ]


def quality_gate(text: str, *, image_url: str = "", slug: str = "") -> list[str]:
    """Deterministic lint check (thin wrapper over ``content_lint.lint_post``).

    Returns human-readable violation strings; empty list = clean. The posting
    path uses the full shared pipeline (:func:`support.quality_gate.
    pre_post_quality_gate`) — this helper stays for direct/audit use.
    """
    from rugmunchbot.support.content_lint import lint_post

    post = {"text": text, "slug": slug or "daily-rug-update", "image_url": image_url}
    return [str(v) for v in lint_post(post)]


def cleanup_html(text: str, *, max_chars: int = DAILY_RUG_UPDATE_MAX_CHARS) -> str:
    """Normalize LLM output to safe Telegram HTML (delegates to the shared
    daily_market_summary cleaner — same allowlist, same trim rules)."""
    from rugmunchbot.channels.daily_market_summary._publish import cleanup_html as _clean

    return _clean(text, max_chars=max_chars)


def _edition_slug() -> str:
    return "daily-rug-update"


async def publish_to_feed(report: str, *, title: str | None = None) -> dict[str, Any]:
    """Publish the update to the rugmunch.io feed (Ghost content platform).

    Same ``create_post`` path the Morning Brief uses (``post_type="briefing"``).
    Fail-open: errors log and return ``{"error": ...}``.
    """
    stamp = datetime.now(UTC).strftime("%B %d, %Y")
    try:
        from rugmunchbot._vendor.content_platform import create_post

        return await create_post(
            content=report,
            post_type="briefing",
            title=title or f"{EDITION} — {stamp}",
            tags=["daily", "rug-update"],
        )
    except Exception as exc:
        logger.warning("daily_rug_update:feed_publish_failed err=%s", str(exc)[:200])
        return {"error": str(exc)}


class _ImagePost(str):
    """A report part carrying an ``image_url`` — duck-types ``NewsPost`` so
    the shared ``_post_photo_aware`` sender attaches the photo."""

    image_url: str = ""

    def __new__(cls, text: str, image_url: str) -> _ImagePost:
        obj = super().__new__(cls, text)
        obj.image_url = image_url
        return obj


def _split_for_telegram(report: str, max_chars: int = DAILY_RUG_UPDATE_MAX_CHARS) -> list[str]:
    """Split an HTML post into Telegram-sized parts at line boundaries."""
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


async def post_daily_rug_update(ctx: Any) -> int:
    """Gather → LLM-write → quality gate → feed mirror → post to main channel.

    Returns 1 when the Telegram post shipped, 0 otherwise. Fails open at
    every step.
    """
    from rugmunchbot.channels.scheduler._core import _chat_id, _post_photo_aware, _safe_send

    chat_id = _chat_id("main")  # registry ``main`` → @cryptorugmuncher
    if not chat_id:
        logger.info("daily_rug_update:no_main_chat_id_skipped")
        return 0

    context = await gather_rug_update_data()
    if not context.get("sources"):
        logger.info("daily_rug_update:no_data_sources_skipped")
        return 0

    messages = build_messages(context)
    try:
        from rugmunchbot.llm_config import chat_with_fallback

        raw = await chat_with_fallback(messages, "long")
    except Exception as exc:
        logger.warning("daily_rug_update:llm_failed err=%s", str(exc)[:160])
        raw = ""
    for marker in ("AI DISCLOSURE:", "OUTPUT FORMAT", "MASTHEAD ("):
        if marker in raw:
            raw = raw.split(marker, 1)[0].rstrip()
    if len(raw.strip()) < 800:
        logger.warning("daily_rug_update:llm_short retrying chars=%s", len(raw.strip()))
        retry_msgs = build_messages(context)
        retry_msgs[1]["content"] += (
            "\n\nIMPORTANT: your previous attempt was too short. Write the FULL "
            "2500-3500 character update now — every section, every item, with links."
        )
        try:
            from rugmunchbot.llm_config import chat_with_fallback

            raw2 = await chat_with_fallback(retry_msgs, "long")
            if len(raw2.strip()) > len(raw.strip()):
                raw = raw2
        except Exception as exc:
            logger.warning("daily_rug_update:llm_retry_failed err=%s", str(exc)[:120])
    if not raw or not raw.strip():
        logger.info("daily_rug_update:llm_empty_skipped sources=%s", context.get("sources"))
        return 0

    report = cleanup_html(raw)
    if not report:
        logger.info("daily_rug_update:cleaned_empty_skipped")
        return 0

    # HARD quality gate (shared pipeline, 2026-08-31): lint → autofix
    # (dedupe links, placeholder image) → ONE stricter-prompt regeneration →
    # block. A blocked update never mirrors and never ships.
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    async def _regenerate_report(bad_text: str, violations: list[Any]) -> str:
        del bad_text
        detail = "; ".join(f"{v.rule}: {v.phrase}" for v in violations[:6])
        retry_msgs = build_messages(context)
        retry_msgs[1]["content"] += (
            "\n\nSTRICT REWRITE REQUIRED — your previous draft failed the "
            f"content quality lint ({detail[:300]}). Rules: no banned phrases, "
            "each URL appears AT MOST ONCE, at least 20 words of original "
            "commentary, no '[SEV] Title / Source:' template shape. "
            "Rewrite the FULL update now."
        )
        from rugmunchbot.llm_config import chat_with_fallback

        return await chat_with_fallback(retry_msgs, "long")

    gate = await pre_post_quality_gate(
        report,
        image_url=pick_update_image(context),
        source=EDITION,
        kind="rug_update",
        regenerate=_regenerate_report,
        logger=logger,
    )
    if not gate.ok:
        logger.warning(
            "daily_rug_update:quality_gate_blocked violations=%d",
            len(gate.violations),
        )
        return 0
    report = gate.text
    image_url = gate.image_url

    # Feed mirror FIRST so the Telegram closer can deep-link the exact post
    # (feed.rugmunch.io/p/{slug}). Ghost publish (website feed) is additive.
    feed_url = ""
    slug = ""
    if DAILY_RUG_UPDATE_FEED_ENABLED:
        try:
            from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

            slug = (
                mirror_post_to_feed(
                    {
                        "text": report[:3900],
                        "story_ref": f"daily-rug-update-{datetime.now(UTC).strftime('%Y%m%d')}",
                        "source": "rugmunch.io",
                        "kind": "rug-update",
                        "image_url": image_url,
                    }
                )
                or ""
            )
        except Exception as exc:
            logger.warning("daily_rug_update:feed_mirror_failed err=%s", str(exc)[:120])
        if slug:
            feed_url = f"https://feed.rugmunch.io/p/{slug}"
        ghost = await publish_to_feed(raw)
        ghost_url = str(ghost.get("url") or "") if isinstance(ghost, dict) else ""
        if ghost_url and not feed_url:
            feed_url = ghost_url
        logger.info("daily_rug_update:feed_published slug=%s url=%s", slug, feed_url)

    # Deterministic closer: one link to the exact mirrored post (never a
    # duplicate — the report body never links the feed URL).
    closer = (
        f"📖 <b>{EDITION}</b> — full write-up on rugmunch.io"
        + (f' — <a href="{feed_url}">read it here</a>.' if feed_url else ".")
        + "\n🤖 Live token intel, scans &amp; alerts: @rugmunchbot"
    )
    parts = _split_for_telegram(report)
    parts[-1] = parts[-1].rstrip() + "\n\n" + closer

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
        else:
            break
    if sent == 0:
        return 0
    logger.info("daily_rug_update:posted parts=%d/%d slug=%s", sent, len(parts), slug)
    return 1
