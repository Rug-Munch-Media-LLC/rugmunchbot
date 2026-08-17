#!/usr/bin/env python3
"""RugMunchBot -- answer delivery for the free-text AI pipeline.

Split out of ``_routing.py`` during the de-godding pass. This module owns
the SEND path of the chat pipeline: keeping the Telegram typing indicator
alive, the interim "still digging" placeholder edit, the untrusted-LLM
answer render + chunked delivery (``_send_answer``) and the combined
send + structured-trace helper (``_send_and_trace_answer``).

``_routing.py`` re-exports ``_send_answer``, ``_send_and_trace_answer``,
``_edit_placeholder_if_slow`` and ``_keep_typing`` (tests import some of
them from ``_routing`` / ``chat_ai``), so every public name and signature
is unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from html import escape
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.handlers.chat_ai._classify import _is_deep_question
from app.domains.telegram.rugmunchbot.handlers.chat_ai._format import _render_bridge_actions
from app.domains.telegram.rugmunchbot.handlers.chat_ai._helpers import _STILL_DIGGING
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._sources import (
    _internal_source_label,
    _normalize_source_domain,
    _scrub_internal_api_refs,
    _source_display_name,
    _source_is_ours,
    _source_type_label,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._markup import (
    CHUNK_MAX_CHARS,
    _beautify_answer,
    _cap_answer,
    _markdown_to_html,
    _normalize_llm_markup,
    _tables_to_lists,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._parts import send_in_parts, split_in_parts
from app.domains.telegram.rugmunchbot.handlers.chat_ai._quality import strip_think_blocks
from app.domains.telegram.rugmunchbot.handlers.chat_ai._trace import trace_answer
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Message

    from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")


def _grounding_source_labels(
    *,
    provider: str,
    rag_count: int,
    bridge_sources: list[dict[str, str]] | None = None,
    live_results: dict[str, ToolResult] | None = None,
    rag_result: ToolResult | None = None,
    has_market_context: bool = False,
    max_labels: int = 4,
) -> tuple[list[tuple[str, bool]], int, int]:
    """Distinct, real source names behind an answer + the footer count.

    Returns ``(labels, count, distinct)``:
      * ``labels`` — up to ``max_labels`` distinct ``(name, is_ours)`` pairs
        for the "Grounded in N sources: …" footer. URL-bearing sources are
        de-duplicated by normalized domain (3 hits from coingecko.com render
        as ONE "coingecko.com"); bare sources de-duplicate by display name
        (two RAG hits both render as ONE "RMI knowledge base"). Our own
        intel — RAG KB, scanner, market digest, backend tier-1 RAG — carries
        an RMI brand label so the community sees we have our own data;
        external providers (DexScreener, CoinGecko, web) stay plain.
      * ``count`` — the footer number, NEVER a raw hit count. Usually equals
        ``distinct``; the one exception is the backend path, which reports
        only ``X-RAG-Context`` (a count, no names) — when no other named
        source exists the count stays the backend's real hit number and the
        generic "RMI knowledge base" label names the source type.
      * ``distinct`` — the true distinct-name count (``len(seen)``), used to
        render the "… +N more" truncation marker when ``labels`` hit the cap.
        Zero when nothing fed the answer.
    """
    bridge_sources = bridge_sources or []
    live_results = live_results or {}
    # Canonical names for OUR OWN live-tool surfaces so the same internal
    # capability never double-counts (live security_scan == bridge scanner).
    _our_tool_labels = {
        "rag_lookup": "RMI knowledge base",
        "security_scan": "RMI scanner",
        "wallet_analysis": "RMI wallet analytics",
        "web_search": "RMI web search",
    }
    seen: set[str] = set()
    labels: list[tuple[str, bool]] = []
    distinct = 0

    def _add(name: str, is_ours: bool) -> None:
        nonlocal distinct
        name = (name or "").strip()
        if not name:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        distinct += 1
        if len(labels) < max_labels:
            labels.append((name, is_ours))

    def _bridge_label(s: dict[str, str]) -> tuple[str, bool]:
        stype = str(s.get("type") or "").strip().lower()
        is_ours = _source_is_ours(s)
        name = _source_display_name(s)
        # Our-own sources (rag/scanner/market/backend) with no URL render as
        # their TYPE label ("RMI knowledge base") so two KB hits collapse
        # into ONE named source — queries stay in the message-level Sources
        # block, the footer names the source. With a URL the real name/domain
        # wins (e.g. a cited article inside our KB).
        if name and (not is_ours or str(s.get("url") or "").strip()):
            return name, is_ours
        label = _source_type_label(stype)
        # A non-ours bare source must never render as "RMI data".
        if not is_ours and label == "RMI data":
            label = "web search" if stype == "web" else "source"
        return label, is_ours

    # Bridge / router sources (dedupe by normalized domain).
    for s in bridge_sources:
        if not isinstance(s, dict):
            continue
        url = str(s.get("url") or "").strip()
        domain = _normalize_source_domain(url) if url else ""
        if domain and domain in seen:
            continue
        if domain:
            seen.add(domain)
        name, is_ours = _bridge_label(s)
        _add(name, is_ours)

    # Live tools that returned REAL data (a knowledge-fallback web answer is
    # synthesized, not a data source — never named).
    for tool, r in live_results.items():
        if not r.ok:
            continue
        src = str(r.source or "").strip()
        if tool == "web_search" and src.startswith("knowledge"):
            continue
        if tool == "rag_lookup":
            _add("RMI knowledge base", True)
        elif src.startswith(("backend:", "bridge:")):
            # Our own endpoints (quick scanner, wallet analytics, bridge).
            # Map the raw internal key to its RMI brand label first; fall
            # back to the tool name so no /api/ path ever reaches the footer.
            label = _internal_source_label(src) or _our_tool_labels.get(tool, f"RMI {tool.replace('_', ' ')}")
            _add(label, True)
        elif src:
            key = src.lower()
            # Same provider already named via a bridge domain (live "coingecko"
            # vs bridge "coingecko.com") — never double-count it.
            if not any(key in d or d in key for d in seen if "." in d):
                _add(src, False)
        else:
            _add(tool.replace("_", " "), False)

    # Direct RAG hit on the fallback path with no typed bridge sources.
    if rag_result is not None and rag_result.ok:
        _add("RMI knowledge base", True)

    # The daily market/news digest grounded the answer.
    if has_market_context:
        _add("RMI market digest", True)

    # Backend tier-1: the real X-RAG-Context count, one generic label. When it
    # is the ONLY source the backend's own hit number is the count (it is the
    # only signal of how much grounding fed the answer) — with other named
    # sources the count is the distinct set (RAG KB counts once).
    if provider == "backend" and int(rag_count) > 0:
        _add("RMI knowledge base", True)
        if distinct <= 1:
            return labels, max(int(rag_count), 1), distinct

    return labels, distinct, distinct


async def _keep_typing(chat: Any) -> None:
    """Keep the Telegram typing indicator alive until cancelled.

    ``chat.send_action("typing")`` is python-telegram-bot's shorthand for the
    Bot API ``sendChatAction`` call — the exact same server-side mechanism
    that Telethon drives with ``functions.messages.SetTypingRequest(
    peer=<chat>, action=types.SendMessageTypingAction())``. (This bot is PTB;
    the Telethon client is only used by the headless user-session for group
    management, never for chat replies.)

    Telegram expires the typing bubble after ~5 seconds. LLM calls take
    10-30s on CPU, so we re-send every 4s to keep the bubble visible. The
    caller cancels this task the moment the answer is about to be sent.
    """
    while True:
        with contextlib.suppress(Exception):
            await chat.send_action("typing")
        try:
            await asyncio.sleep(4)
        except asyncio.CancelledError:
            break


async def _edit_placeholder_if_slow(placeholder: Message, delay: float = 5.0) -> None:
    """After ``delay`` seconds, swap the thinking placeholder for a
    "still digging" note so long backend queries don't look stalled.

    The task is cancelled by the caller the moment the backend answers, so a
    fast response never gets clobbered by the interim edit.
    """
    try:
        await asyncio.sleep(delay)
        await placeholder.edit_text(_STILL_DIGGING, parse_mode=ParseMode.HTML)
    except (asyncio.CancelledError, TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("chat_ai interim placeholder edit skipped", exc_info=True)


async def _send_and_trace_answer(
    message: Message,
    preliminary: Message,
    answer: str,
    *,
    user_id: int,
    text: str,
    pipeline_start: float,
    result: dict[str, Any],
    bridge_sources: list[dict[str, str]],
    bridge_actions: list[dict[str, str]],
    live_results: dict[str, ToolResult],
    rag_result: ToolResult | None,
    has_market_context: bool,
    answer_is_degenerate: bool,
    stripped_syms: list[str],
    stripped_addrs: list[str],
) -> None:
    """Dismiss the "Thinking…" placeholder, send the answer as NEW messages
    (grounding footer + bridge action buttons on the last chunk) and emit the
    structured answer trace (JSON line via structlog).

    The placeholder is DELETED (never edited to a bare ✅ — that reads as a
    glitch and doubles as a stray message once the real answer lands).
    """
    with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
        await preliminary.delete()
    # Truthful grounding footer: name the DISTINCT intelligence sources that
    # actually fed the answer (RAG + live tools + web + market context) and
    # count them — never a raw hit count, never a fabricated number. A
    # replaced degenerate answer has no real grounding.
    source_labels: list[tuple[str, bool]] = []
    grounding_count = 0
    distinct_sources = 0
    if not answer_is_degenerate:
        source_labels, grounding_count, distinct_sources = _grounding_source_labels(
            provider=str(result.get("provider") or ""),
            rag_count=int(result.get("rag_count") or 0),
            bridge_sources=bridge_sources,
            live_results=live_results,
            rag_result=rag_result,
            has_market_context=has_market_context,
        )
    parts_sent = await _send_answer(
        message,
        answer,
        grounding_count,
        source_labels=source_labels,
        distinct_sources=distinct_sources,
        actions=bridge_actions,
    )

    # ── Structured answer trace (JSON line via structlog) ────────────────
    # One machine-parseable event per answer: question, intent detected,
    # tools called, data sources hit, fabricated tokens (must be 0), latency,
    # LLM used — greppable directly in the bot logs.
    data_sources: list[str] = []
    for r in live_results.values():
        if r.ok and r.source:
            data_sources.append(r.source)
    if result.get("provider") == "backend" and result.get("rag_count"):
        data_sources.append("backend-rag")
    data_sources.extend(str(s.get("type")) for s in bridge_sources if isinstance(s, dict) and s.get("type"))
    trace_answer(
        question=text,
        user_id=user_id,
        intent="analysis" if _is_deep_question(text) else "chat",
        route=str(result.get("provider") or ""),
        tools=sorted(live_results.keys()),
        sources=data_sources,
        fabricated_symbols=stripped_syms,
        fabricated_addresses=stripped_addrs,
        latency_ms=(time.monotonic() - pipeline_start) * 1000,
        llm=str(result.get("model") or ""),
        provider=str(result.get("provider") or ""),
        rag_count=int(result.get("rag_count") or 0),
        parts=parts_sent,
    )


async def _send_answer(
    message: Message,
    answer: str,
    grounding_count: int,
    *,
    source_labels: list[tuple[str, bool]] | None = None,
    distinct_sources: int | None = None,
    actions: list[dict[str, str]] | None = None,
) -> int:
    """Reply with the answer, split into Telegram-sized parts. Returns the
    number of parts actually sent (0 when there is nothing to send).

    v10 pipeline (all on the RAW text first, then escape):
      1. ``_normalize_llm_markup`` converts any RAW HTML the model emitted
         anyway (``<b>`` → ``**``, ``&amp;`` → ``&``) into markdown —
         defense-in-depth so escape() can never turn a real tag into
         literal garbage or unbalanced HTML.
      2. ``strip_think_blocks`` removes any leaked ``<think>`` reasoning
         chains (defense in depth — ``chat_ai_response`` already strips).
      3. ``_tables_to_lists`` converts markdown pipe tables to compact
         bullet lists (Telegram HTML has no table element).
      4. ``_beautify_answer`` normalizes emoji section headers and inserts
         thin separators beneath them.
      5. ``split_in_parts`` (``handlers/chat_ai/_parts.py``) splits at
         paragraph / section-header boundaries, never mid-sentence, never
         mid-link, never mid-``**bold**`` span, and never mid-code-block.
      6. Each part is HTML-escaped (LLM output is not trusted HTML).
      7. ``_markdown_to_html`` converts the markdown the persona / tool
         answers emit — ``**bold**``, `` `code` ``, ``*italic*``, plus block
         constructs (``##`` headers, ``---`` separators, ``1.`` lists, pipe
         tables) — into real ``<b>...</b>`` / ``<code>...</code>`` /
         ``<i>...</i>``, safe because the content was already escaped in
         step 6.
      8. ``send_in_parts`` delivers each part as its own message (keyboard on
         the last part; plain-text degrade on parse rejection).
    A short "⚡ Grounded in N sources: <names> | Rug Munch Bot" footer is
    appended to the last chunk when the answer had real grounding.
    ``grounding_count`` is the number of DISTINCT intelligence sources that
    actually fed the answer (see ``_grounding_source_labels`` — the same
    function produces ``source_labels``, the real named sources: external
    providers like DexScreener/CoinGecko plus our own intel, RMI-branded).
    When ``source_labels`` is omitted (direct callers) the count-only
    "intelligence sources" wording is kept. Counts above 100 with no names
    are shown as "hundreds of sources". Names are HTML-escaped here — the
    footer is appended AFTER the escape pass, so untrusted bridge metadata
    can never inject markup. Zero sources → no footer at all.
    ``actions`` (bridge-suggested buttons) are rendered as an inline
    keyboard on the last chunk when present; otherwise the back menu is used.
    """
    raw = _beautify_answer(
        _tables_to_lists(strip_think_blocks(_scrub_internal_api_refs(_normalize_llm_markup(answer))))
    )
    # No 1000-word walls: a rambling answer is trimmed to a sane length at a
    # paragraph/sentence boundary (with an ellipsis) BEFORE the Telegram
    # chunker — a simple query must never become a multi-part monologue.
    raw = _cap_answer(raw)
    chunks = split_in_parts(raw, max_chars=CHUNK_MAX_CHARS)
    if not chunks:
        return 0
    escaped = [escape(c) for c in chunks]
    html = [_markdown_to_html(c) for c in escaped]
    if grounding_count > 0:
        if source_labels:
            # Real named sources: external providers plain, our own intel
            # already RMI-branded (e.g. "RMI knowledge base"). Names are
            # untrusted bridge metadata — scrub internal API refs then escape
            # AFTER the answer escape pass so they can never inject HTML into
            # the footer.
            names = ", ".join(escape(_scrub_internal_api_refs(name)) for name, _is_ours in source_labels)
            if distinct_sources is not None and distinct_sources > len(source_labels):
                names += f", \u2026 +{distinct_sources - len(source_labels)} more"
            count_word = "source" if grounding_count == 1 else "sources"
            footer = f"Grounded in {grounding_count} {count_word}: {names}"
        elif grounding_count > 100:
            footer = "Grounded in hundreds of intelligence sources"
        elif grounding_count == 1:
            footer = "Grounded in 1 intelligence source"
        else:
            footer = f"Grounded in {grounding_count} intelligence sources"
        html[-1] = f"{html[-1]}\n\n<i>\u26a1 {footer} | Rug Munch Bot</i>"

    kb = _render_bridge_actions(actions) or back_kb()
    await send_in_parts(
        message,
        html,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    return len(html)


__all__ = [
    "_edit_placeholder_if_slow",
    "_keep_typing",
    "_send_and_trace_answer",
    "_send_answer",
]
