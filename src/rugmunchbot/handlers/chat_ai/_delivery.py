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
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from rugmunchbot.handlers.chat_ai._classify import _is_deep_question
from rugmunchbot.handlers.chat_ai._delivery_footer import grounding_footer
from rugmunchbot.handlers.chat_ai._format import _render_bridge_actions
from rugmunchbot.handlers.chat_ai._helpers import _STILL_DIGGING
from rugmunchbot.handlers.chat_ai._llm._sources import (
    _internal_source_label,
    _normalize_source_domain,
    _scrub_internal_api_refs,
    _source_display_name,
    _source_is_ours,
    _source_type_label,
)
from rugmunchbot.handlers.chat_ai._markup import (
    CHUNK_MAX_CHARS,
    _beautify_answer,
    _cap_answer,
    _markdown_to_html,
    _normalize_llm_markup,
    _tables_to_lists,
)
from rugmunchbot.handlers.chat_ai._parts import send_in_parts, split_in_parts
from rugmunchbot.handlers.chat_ai._quality import GroundingContext, strip_think_blocks
from rugmunchbot.handlers.chat_ai._trace import trace_answer
from rugmunchbot.keyboards import back_kb
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Message

    from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")


def _grounding_source_labels(
    ctx: GroundingContext, max_labels: int = 4
) -> tuple[list[tuple[str, bool]], int, int]:
    """Distinct, real source names behind an answer + the footer count.

    Args live on ``ctx`` (the shared :class:`GroundingContext`).

    Returns ``(labels, count, distinct)``:
      * ``labels`` — up to ``max_labels`` distinct ``(name, is_ours)`` pairs
        for the "Grounded in N sources: …" footer. URL-bearing sources are
        de-duplicated by normalized domain (3 hits from coingecko.com render
        as ONE "coingecko.com"); bare sources de-duplicate by display name
        (two RAG hits both render as ONE "RMI knowledge base"). Our own
        intel — RAG KB, scanner, market digest — carries
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
    provider = ctx.provider
    rag_count = ctx.rag_count
    bridge_sources = ctx.bridge_sources or []
    live_results = ctx.live_results or {}
    rag_result = ctx.rag_result
    has_market_context = ctx.has_market_context
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
            label = _internal_source_label(src) or _our_tool_labels.get(
                tool, f"RMI {tool.replace('_', ' ')}"
            )
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

    # RAG-KB/backend-label source: the real RAG hit count, one generic label.
    # When it is the ONLY source the RAG hit number is the count (it is the
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


_STILL_DIGGING_TMPL = (
    "\U0001f916 <b>Still digging...</b>\n\n\U0001f4ac <i>{question}</i>\n\n"
    "\u23f3 Cross-referencing exploit databases + checking live news \u2014 full answer coming..."
)


async def _edit_placeholder_if_slow(
    placeholder: Message, delay: float = 5.0, *, question=None
) -> None:
    """After ``delay`` seconds, swap the thinking placeholder for a
    "still digging" note so long backend queries don't look stalled.

    The task is cancelled by the caller the moment the backend answers, so a
    fast response never gets clobbered by the interim edit.
    """
    try:
        await asyncio.sleep(delay)
        note = _STILL_DIGGING
        if question:
            q = str(question)[:90].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            note = _STILL_DIGGING_TMPL.format(question=q)
        await placeholder.edit_text(note, parse_mode=ParseMode.HTML)
    except (asyncio.CancelledError, TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("chat_ai interim placeholder edit skipped", exc_info=True)


def _collect_answer_sources(
    live_results: dict[str, ToolResult],
    result: dict[str, Any],
    bridge_sources: list[dict[str, str]],
) -> list[str]:
    """Distinct data sources that fed the answer, for the structured trace."""
    sources: list[str] = []
    for r in live_results.values():
        if r.ok and r.source:
            sources.append(r.source)
    if result.get("provider") == "backend" and result.get("rag_count"):
        sources.append("backend-rag")
    sources.extend(
        str(s.get("type")) for s in bridge_sources if isinstance(s, dict) and s.get("type")
    )
    return sources


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
            GroundingContext(
                provider=str(result.get("provider") or ""),
                rag_count=int(result.get("rag_count") or 0),
                bridge_sources=bridge_sources,
                live_results=live_results,
                rag_result=rag_result,
                has_market_context=has_market_context,
            )
        )
    parts_sent = await _send_answer(
        message,
        answer,
        grounding_count,
        AnswerExtras(
            source_labels=source_labels, distinct_sources=distinct_sources, actions=bridge_actions
        ),
    )

    # ── Structured answer trace (JSON line via structlog) ────────────────
    # One machine-parseable event per answer: question, intent detected,
    # tools called, data sources hit, fabricated tokens (must be 0), latency,
    # LLM used — greppable directly in the bot logs.
    data_sources = _collect_answer_sources(live_results, result, bridge_sources)
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


@dataclass
class AnswerExtras:
    source_labels: list[tuple[str, bool]] | None = None
    distinct_sources: int | None = None
    actions: list[dict[str, str]] | None = None


async def _send_answer(
    message: Message,
    answer: str,
    grounding_count: int,
    extras: AnswerExtras | None = None,
) -> int:
    """Reply with the answer, split into Telegram-sized parts.

    Returns the number of parts sent (0 when there is nothing to send); args
    live on ``extras`` (an :class:`AnswerExtras`).

    Order matters: the RAW text is normalized (LLM markup, leaked ``<think>``
    blocks, pipe tables, emoji headers), capped, split on paragraph/link/bold
    boundaries (``split_in_parts``), then HTML-escaped, then markdown is turned
    into Telegram HTML. A short grounding footer naming the real sources and
    any bridge-suggested keyboard ride on the last part.
    """
    extras = extras or AnswerExtras()
    source_labels = extras.source_labels
    distinct_sources = extras.distinct_sources
    actions = extras.actions
    raw = _beautify_answer(
        _tables_to_lists(
            strip_think_blocks(_scrub_internal_api_refs(_normalize_llm_markup(answer)))
        )
    )
    # Anti-wall guardrail, not a truncation policy: the cap (14000 chars ≈
    # 3-4 Telegram parts) only trims a genuinely rambling answer at a
    # paragraph/sentence boundary (with an ellipsis). Deep analysis is
    # EXPECTED to span multiple parts — split_in_parts below delivers each
    # part as its own message, so answers complete fully.
    raw = _cap_answer(raw)
    chunks = split_in_parts(raw, max_chars=CHUNK_MAX_CHARS)
    if not chunks:
        return 0
    escaped = [escape(c) for c in chunks]
    html = [_markdown_to_html(c) for c in escaped]
    footer = grounding_footer(grounding_count, source_labels, distinct_sources)
    if footer:
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
