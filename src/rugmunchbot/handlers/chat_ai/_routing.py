#!/usr/bin/env python3
"""RugMunchBot -- chat routing for the free-text AI pipeline.

Extracted from ``chat_ai/__init__.py`` during the de-godding pass (Batch 12).
This module owns the CHAT ROUTING layer of the merged single free-text
pipeline (``ai_chat_handler``): the owner admin console, structural feature
routing (known-scam block / bare-address auto-scan / ``$TOKEN`` price card),
the shared AI core (``chat_ai_response`` — tier-2 canned → tier-1 backend RAG
chat → tier-3 fallback), the answer delivery helpers (``_send_answer`` and
friends) and the one-line ``register_chat_ai`` wiring.

Split into sibling modules:
  * ``_live_tools.py`` — bot-side live tool dispatch + timing budgets
  * ``_guards.py`` — entry guards + answer-quality guardrails + fallback replies
  * ``_llm.py`` — LLM clients + the backend-less fallback orchestration

``chat_ai/__init__.py`` is now a thin re-export shim; every public name and
signature is unchanged.

MONKEYPATCH CONTRACT: the pipeline tests replace ``chat_ai.check_spam``,
``chat_ai.is_evm``/``chat_ai.is_sol``, ``chat_ai._call_backend_chat`` and
``chat_ai._run_live_tools`` on the package, so those lookups go through the
package module at call time instead of module-level bindings.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from html import escape
from typing import TYPE_CHECKING, Any, cast

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError

import rugmunchbot.handlers.chat_ai as chat_ai
from rugmunchbot import anti_abuse, db
from rugmunchbot.admin_state import track_ai_query, track_message, track_tokens
from rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    backend_headers,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from rugmunchbot.daily_context import get_daily_context
from rugmunchbot.formatters import (
    TOKEN_RE,
    detect_chain,
    footer_links,
    is_owner,
    web_scan_button,
)
from rugmunchbot.handlers.chat_ai._classify import (
    _extract_addresses,
    _is_capability_question,
    _is_deep_question,
    _is_greeting_or_thanks,
)
from rugmunchbot.handlers.chat_ai._format import _render_bridge_actions
from rugmunchbot.handlers.chat_ai._guards import (
    _injection_warning,
    _post_guardrail,
    _reply_local_intent,
    _run_entry_guards,
    _scam_address_warning,
)
from rugmunchbot.handlers.chat_ai._helpers import _FALLBACK_MESSAGE, _STILL_DIGGING
from rugmunchbot.handlers.chat_ai._live_tools import _run_rag_lookup
from rugmunchbot.handlers.chat_ai._llm import _build_fallback_answer
from rugmunchbot.handlers.chat_ai._markup import (
    CHUNK_MAX_CHARS,
    _beautify_answer,
    _cap_answer,
    _format_scan_summary,
    _markdown_to_html,
    _normalize_llm_markup,
    _tables_to_lists,
)
from rugmunchbot.handlers.chat_ai._parts import send_in_parts, split_in_parts
from rugmunchbot.handlers.chat_ai._quality import (
    _grounding_sources_count,
    strip_think_blocks,
)
from rugmunchbot.handlers.chat_ai._trace import trace_answer
from rugmunchbot.handlers.message import (
    admin_kb_is_sent,
    admin_kb_mark_sent,
    admin_kb_reset,
)
from rugmunchbot.keyboards import back_kb, owner_admin_reply_kb
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.menus import capability_menu_kb
from rugmunchbot.tools.rag_lookup import should_rag_lookup

if TYPE_CHECKING:
    from telegram import Message
    from telegram.ext import Application, ContextTypes

    from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# ENDPOINTS & BUDGETS — BACKEND / BRIDGE
# ══════════════════════════════════════════════
# Backend RAG chat (tier-1): CHAT_ENDPOINT / CHAT_TIMEOUT live in _llm.py
# next to _call_backend_chat and are re-exported by chat_ai/__init__.py.
SCAN_ENDPOINT = f"{BACKEND_URL}/api/v1/bot/scan"
SCAN_TIMEOUT = 6.0  # quick scan context is best-effort; never block chat on it
MAX_MESSAGE_CHARS = 2000  # backend ChatRequest.message limit

# Chat RAG Bridge — RugBrain/RugMunchScan context + SearXNG web search.
# One call returns live web search, RAG knowledge, market digest, scanner
# context and exploit rundown blocks (fail-open upstream).
BRIDGE_CONTEXT_ENDPOINT = os.getenv("BRIDGE_CONTEXT_ENDPOINT", "http://127.0.0.1:8095/chat/context")
BRIDGE_TIMEOUT = 12.0  # web search can take a few seconds; never block chat on it

# $TOKEN price card decision budget: after the AI answer is ready, the
# concurrently-running DexScreener lookup (fired by ai_chat_handler) gets
# this short window to land before we commit to the AI answer. The old flow
# serialized a 10s HTTP call before the pipeline even started.
TOKEN_CARD_DECIDE_WAIT = 1.0

# ══════════════════════════════════════════════
# OWNER ADMIN CONSOLE (ported from handlers/message.py)
# Persistent reply keyboard pinned below the input box in private chats.
# Button labels are plain text; they are routed to the matching /admin_*
# command so @cryptorugmunch always has one-tap admin access.
# ══════════════════════════════════════════════
_ADMIN_BUTTON_MAP: dict[str, str] = {
    "\U0001f6e1\ufe0f Admin Dashboard": "/admin",
    "\U0001f4ca Stats": "/admin_stats",
    "\U0001f4e2 Broadcast": "/admin_broadcast",
    "\U0001f465 User Lookup": "/admin_user",
    "\U0001f504 Bot ON/OFF": "/admin_toggle",
    "\U0001f4ac Chat ON/OFF": "/admin_toggle_chat",
    "\U0001f3d9\ufe0f Main Menu": "/start",
}


async def _ensure_admin_reply_kb(update: Update) -> None:
    """Attach the persistent admin reply keyboard once per owner DM."""
    chat = update.effective_chat
    if chat is None or admin_kb_is_sent(chat.id) or update.message is None:
        return
    admin_kb_mark_sent(chat.id)
    try:
        await update.message.reply_text(
            "\U0001f6e1\ufe0f <b>Admin console</b>\n"
            "Tap a button below for quick admin actions \u2014 they stay pinned here.",
            parse_mode=ParseMode.HTML,
            reply_markup=owner_admin_reply_kb(),
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        admin_kb_reset(chat.id)
        logger.debug("admin reply keyboard send failed", exc_info=True)


async def _dispatch_admin_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Route an admin reply-keyboard button press to its /admin_* command."""
    from rugmunchbot.handlers.owner import (
        cmd_admin,
        cmd_admin_broadcast,
        cmd_admin_stats,
        cmd_admin_toggle,
        cmd_admin_toggle_chat,
        cmd_admin_user,
    )
    from rugmunchbot.handlers.scanning import cmd_start

    cmd = _ADMIN_BUTTON_MAP[text]
    ctx.args = []
    if cmd == "/admin":
        await cmd_admin(update, ctx)
    elif cmd == "/admin_stats":
        await cmd_admin_stats(update, ctx)
    elif cmd == "/admin_broadcast":
        await cmd_admin_broadcast(update, ctx)
    elif cmd == "/admin_user":
        await cmd_admin_user(update, ctx)
    elif cmd == "/admin_toggle":
        await cmd_admin_toggle(update, ctx)
    elif cmd == "/admin_toggle_chat":
        await cmd_admin_toggle_chat(update, ctx)
    elif cmd == "/start":
        # /start re-attaches the persistent keyboard below the new message.
        admin_kb_reset(update.effective_chat.id)  # type: ignore[union-attr]  # PTB None-able fields
        await cmd_start(update, ctx)


async def _fetch_scan_context(address: str) -> str | None:
    """Best-effort quick scanner read → compact risk summary line.

    ``POST /api/v1/bot/scan`` is Redis-cached and fast; on any failure or
    missing data it returns ``None`` so chat never blocks on the scanner.
    """
    chain = detect_chain(address)
    try:
        async with httpx.AsyncClient(timeout=SCAN_TIMEOUT) as client:
            r = await client.post(
                SCAN_ENDPOINT,
                headers=backend_headers(),
                json={"address": address, "chain": chain},
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("scan context unavailable (addr=%.16s): %s", address, e)
        return None
    if r.status_code != 200:
        logger.debug("scan context HTTP %d (addr=%.16s)", r.status_code, address)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict) or raw.get("error"):
        return None
    data = cast("dict[str, Any]", raw)
    return _format_scan_summary(address, data)


async def _fetch_bridge_context(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, list[dict[str, str]], list[dict[str, str]]] | None:
    """Live context from the Chat RAG Bridge (port 8095, fail-open).

    Returns ``(blocks, sources, actions)``:
      * ``blocks`` — one compact block combining the bridge's upstream
        context: SearXNG web search (live internet), RAG knowledge, market
        digest, token scanner and exploit rundown.
      * ``sources`` — the bridge's grounding citation metadata
        (``[{"type": "web"|"rag"|"scanner"|"market", ...}]``) used to render
        the real "Grounded in N intelligence sources" footer + source lines.
      * ``actions`` — suggested inline-button actions (deep-dive on a token,
        exploit rundown) built by the bridge's rundown block.

    ``None`` on any failure so chat never blocks on it.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(BRIDGE_TIMEOUT, connect=3.0, read=BRIDGE_TIMEOUT)) as client:
            r = await client.post(
                BRIDGE_CONTEXT_ENDPOINT,
                json={"message": message[:8000], "history": history},
            )
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError) as e:
        logger.debug("bridge context unavailable (user msg %.40s): %s", message, e)
        return None
    if r.status_code != 200:
        logger.debug("bridge context HTTP %d (user msg %.40s)", r.status_code, message)
        return None
    try:
        raw = r.json()
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    blocks = raw.get("blocks") if isinstance(raw.get("blocks"), dict) else {}
    if not blocks:
        return None
    # Order matters: scanner (address-specific) > web (live) > rag > market.
    parts: list[str] = []
    for key in ("scanner_context", "web_context", "rag_context", "market_context", "rundown_context"):
        val = (blocks.get(key) or "").strip()
        if val:
            # Strip meta-instructions the bridge embeds for ITS OWN llama call
            # ("introduce these with 'I found this on the web:'") — those leak
            # into our prompt and make the model parrot context instead of
            # answering. Keep the data, drop the stage directions.
            val = re.sub(
                r"\[[^\]]*\]\s*Fresh web results \(current[^)]*\)[:\-]?\s*",
                "[Web results (live)]\n",
                val,
            )
            val = re.sub(
                r"introduce these with ['\"]I found this on the web:['\"]\s*",
                "",
                val,
            )
            val = re.sub(r"\n{3,}", "\n\n", val).strip()
            if val:
                parts.append(val)
    if not parts:
        return None

    raw_sources = raw.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    sources = [s for s in sources if isinstance(s, dict)]
    raw_actions = raw.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    actions = [a for a in actions if isinstance(a, dict)]

    return "\n\n".join(parts), sources, actions


async def _keep_typing(chat: Any) -> None:
    """Keep the Telegram typing indicator alive until cancelled.

    Telegram expires send_action('typing') after ~5 seconds. LLM calls take
    10-30s on CPU, so we re-send every 4s to keep the bubble visible.
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


# ══════════════════════════════════════════════
# $TOKEN SYMBOL LOOKUP (ported from handlers/message.py)
# ══════════════════════════════════════════════


class _TokenCardFlag:
    """Shared mutable holder for the ``$TOKEN`` card-sent signal (P2 fix).

    ``_lookup_token_symbol`` sets ``sent = True`` the moment a price card is
    delivered. The AI pipeline checks the flag right before sending any
    reply, so a card that lands inside the ``_token_card_won`` decide-wait
    (or just after its timeout raced with ``_cancel_token_lookup``) can never
    double-send with the AI answer.
    """

    __slots__ = ("sent",)

    def __init__(self) -> None:
        self.sent = False


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort numeric coercion for DexScreener payload fields (P2 fix).

    DexScreener can return ``None``, absent or non-numeric values (e.g.
    ``"N/A"``) for price / volume / market cap — a raw ``float(...)`` would
    crash the ``$TOKEN`` card. Fail-open to ``default`` on any malformed
    input.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _lookup_token_symbol(text: str, message: Message, sent_flag: _TokenCardFlag | None = None) -> bool:
    """Instant ``$TOKEN`` price card via DexScreener.

    Returns True when a price card was sent; False when there is no symbol
    or the lookup failed so the caller falls through to the AI pipeline
    (matches the old handle_message behavior).

    ``sent_flag``: optional shared holder (see :class:`_TokenCardFlag`);
    ``sent`` is set True the moment a card is delivered so the AI pipeline
    can detect a card that landed inside the decide-wait / after its
    timeout and never double-send (P2 race fix).
    """
    token_match = TOKEN_RE.search(text)
    if not token_match:
        return False
    symbol = token_match.group(1).upper()
    with contextlib.suppress(Exception):
        await message.chat.send_action("typing")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{DEXSCREENER}/search/?q={symbol}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    addr = p.get("baseToken", {}).get("address", "")
                    chain = p.get("chainId", "")
                    price = _safe_float(p.get("priceUsd", 0))
                    h24 = p.get("priceChange", {}).get("h24") or 0
                    vol = _safe_float(p.get("volume", {}).get("h24") or 0)
                    mcap = _safe_float(p.get("marketCap") or 0)
                    name = p.get("baseToken", {}).get("name", symbol)
                    quick_text = (
                        f"\U0001fa99 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                        f"<code>{addr}</code>\n\n"
                        f"\U0001f4b0 {fmt_number(price)} | \U0001f504 24h: {fmt_pct(h24)}\n"
                        f"\U0001f4c8 MCap: {fmt_number(mcap)} | \U0001f4a7 Vol: {fmt_number(vol)}"
                        f"{footer_links()}"
                    )
                    if addr:
                        await message.reply_text(
                            quick_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}"
                                        )
                                    ],
                                    [web_scan_button(addr, chain)],
                                ]
                            ),
                            disable_web_page_preview=True,
                        )
                        if sent_flag is not None:
                            sent_flag.sent = True
                        return True
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
        TelegramError,
    ) as e:
        logger.debug("chat_ai $TOKEN lookup failed for %s: %s", symbol, e)
    return False


async def _token_card_won(token_lookup_task: asyncio.Task[bool] | None) -> bool:
    """True when the concurrent ``$TOKEN`` DexScreener lookup sent a price card.

    Called only after the AI answer is ready. Waits at most
    ``TOKEN_CARD_DECIDE_WAIT`` for the lookup so a slow DexScreener never
    stalls the AI answer; an already-finished lookup returns instantly. The
    caller decides from the result: a landed price card IS the answer, so the
    AI answer is skipped. Fail-open — a broken lookup never blocks chat.
    """
    if token_lookup_task is None:
        return False
    try:
        return bool(await asyncio.wait_for(asyncio.shield(token_lookup_task), timeout=TOKEN_CARD_DECIDE_WAIT))
    except Exception:
        # Timeout or unexpected failure: the card loses the race to the AI
        # answer. (CancelledError is a BaseException and propagates — this
        # coroutine was cancelled by its caller and must unwind cleanly.)
        return False


async def _cancel_token_lookup(token_lookup_task: asyncio.Task[bool] | None) -> None:
    """Cancel the concurrent ``$TOKEN`` lookup when another path already replied.

    Keeps the lookup from firing a late price card after a canned greeting /
    guard reply, and prevents a dangling task on the early-return paths.
    """
    if token_lookup_task is not None and not token_lookup_task.done():
        token_lookup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await token_lookup_task


# ══════════════════════════════════════════════
# FEATURE ROUTING (free text only, NO AI quota)
# ══════════════════════════════════════════════


async def _route_structural(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    text: str,
    user: Any,
    *,
    skip_token_lookup: bool = False,
) -> bool:
    """Free-text feature routing that does NOT consume the AI quota.

    Ported from the old ``handlers/message.py handle_message`` so features
    survive the dedup: known-scam block, bare-address auto-scan, and the
    ``$TOKEN`` instant price card. Returns True when the message was fully
    handled (a reply was sent) and the caller should stop.

    ``skip_token_lookup``: :func:`ai_chat_handler` fires the ``$TOKEN``
    DexScreener lookup CONCURRENTLY with the AI pipeline (so a slow
    DexScreener never serializes 10s before chat) and passes this flag so the
    lookup is not run twice. Direct callers keep the synchronous behavior.

    The old natural-language-address-extraction block is NOT ported: it was
    dead code (EVM_RE/SOL_RE are whole-string anchored, so it could never
    fire after the bare-address check) and embedded addresses in questions
    are now handled better by :func:`chat_ai_response` address enhancement
    (risk summary prepended to the AI prompt).
    """
    message = update.message
    if message is None:
        return False

    # 1. Known scam addresses are blocked before any scan or AI call.
    #    Uses _extract_addresses (unanchored) so embedded scam addresses are
    #    caught too — strictly more protective than the old anchored check.
    for addr in _extract_addresses(text):
        if anti_abuse.is_known_scam_address(addr):
            logger.info("known-scam address blocked user=%s addr=%.16s", user.id, addr)
            await _scam_address_warning(update, addr)
            return True

    # 2. Bare contract address (the whole message is the address) → full /scan.
    if chat_ai.is_evm(text) or chat_ai.is_sol(text):
        ctx.args = [text]
        from rugmunchbot.handlers.scanning import cmd_scan

        await cmd_scan(update, ctx)
        return True

    # 3. $TOKEN symbol lookup → instant DexScreener price card.
    #    ai_chat_handler fires this CONCURRENTLY with the AI pipeline
    #    (skip_token_lookup=True) so a slow DexScreener never blocks chat.
    if skip_token_lookup:
        return False
    return bool(await _lookup_token_symbol(text, message))


async def _persist_answer(user_id: int, text: str, answer: str) -> None:
    """Persist the user/assistant messages + usage/analytics counters."""
    db.increment_usage(user_id, ai_msg=True)
    db.add_chat_message(user_id, "user", text)
    db.add_chat_message(user_id, "assistant", answer)
    with contextlib.suppress(Exception):
        await track_ai_query()
        await track_tokens(len(answer) // 4)


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
    # Truthful grounding footer: count the DISTINCT intelligence sources that
    # actually fed the answer (RAG + live tools + web + market context), not
    # just RAG hits. A replaced degenerate answer has no real grounding.
    grounding_count = (
        0
        if answer_is_degenerate
        else _grounding_sources_count(
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


async def chat_ai_response(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    text: str,
    user: Any,
    *,
    token_lookup_task: asyncio.Task[bool] | None = None,
    token_card_flag: _TokenCardFlag | None = None,
) -> bool:
    """Run the merged AI chat pipeline. Returns True when a reply was sent.

    Shared by the free-text tail of :func:`ai_chat_handler`, /chat
    (handlers/chat.py) and /ask (handlers/ask.py). Enforces the weekly
    AI-message quota per tier, per-user flood limits, and answers through
    the backend RAG chat (auto-RAG with news_v2).

    ``token_lookup_task`` is the concurrently-fired ``$TOKEN`` DexScreener
    lookup (from :func:`ai_chat_handler`): when the price card lands before
    the AI answer is ready, it IS the answer and the AI answer is skipped.
    ``None`` for /chat and /ask (no structural routing there).

    ``token_card_flag`` is the shared ``sent`` signal owned by the same
    lookup (see :class:`_TokenCardFlag`): checked right before any reply so
    a card that lands just after the decide-wait never double-sends (P2
    race fix). ``None`` for /chat and /ask.

    Same signature as the old ``handlers.message.ai_chat_response`` it
    replaces, so callers could be switched without rework (both new params
    are optional keyword-only).
    """
    message = update.message
    if message is None or not text.strip():
        return False

    u = db.get_or_create_user(user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return True
    if chat_ai.check_spam(user.id):
        return True

    # End-to-end answer latency is measured from here (all paths that send an
    # answer trace it via :func:`trace_answer`).
    pipeline_start = time.monotonic()

    # Pre-LLM guards: chat_enabled toggle -> injection -> quota -> flood ->
    # gibberish (tier-3 redirect). True = message already handled.
    if await _run_entry_guards(update, message, user, text, pipeline_start=pipeline_start):
        await _cancel_token_lookup(token_lookup_task)
        return True

    # Tier-2: ONLY instant-reply for greetings, thanks, and capability inquiries.
    # Everything else goes to the LLM — the bot should answer what the user asks.
    # Capability questions ("what can you do?") attach the capability menu so
    # the bot's surface is discoverable right from the first answer.
    capability_menu = capability_menu_kb() if _is_capability_question(text) else None
    if (_is_greeting_or_thanks(text) or _is_capability_question(text)) and await _reply_local_intent(
        message, text, user, menu_kb=capability_menu
    ):
        await _cancel_token_lookup(token_lookup_task)
        return True

    # Thumbs up — instant acknowledgment
    with contextlib.suppress(Exception):
        from telegram import ReactionTypeEmoji

        await message.set_reaction([ReactionTypeEmoji(emoji="👍")])

    # Start typing indicator — Telegram typing expires after ~5s, so we
    # keep it alive in a background task until the answer is ready.
    typing_task = asyncio.create_task(_keep_typing(message.chat))

    # Preliminary "thinking" message — keeps user engaged
    preliminary = await message.reply_text(
        "\U0001f916 <b>Thinking...</b>\n\n"
        f"\U0001f4ac <i>{escape(text[:150])}</i>\n\n"
        "\u23f1\ufe0f Working on a detailed answer...",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    # Interim "still digging" note: fired ~5s after the placeholder so a slow
    # backend query doesn't look stalled. Cancelled the moment the answer is
    # ready (below), so a fast response never gets clobbered by the edit.
    placeholder_task = asyncio.create_task(_edit_placeholder_if_slow(preliminary))

    # P1 (task-lifecycle leak fix): every task created below must be
    # cancelled in the finally block — an early exception (backend RAG
    # failure, guardrail crash, send failure) must never leak a dangling
    # coroutine: the backend RAG call, the bridge/daily/scan context fetches,
    # the live tools, the direct RAG lookup and the caller-owned $TOKEN card
    # lookup (a late card firing on an exception path would be a stray
    # message). typing_task / placeholder_task are created above and are
    # cancelled too.
    backend_task: asyncio.Task[Any] | None = None
    daily_ctx_task: asyncio.Task[Any] | None = None
    scan_ctx_task: asyncio.Task[Any] | None = None
    bridge_ctx_task: asyncio.Task[Any] | None = None
    live_tools_task: asyncio.Task[Any] | None = None
    rag_task: asyncio.Task[Any] | None = None
    try:
        # CONCURRENT: fire the backend RAG chat (tier-1) AND the fallback context
        # fetches at the same time. The backend RAGs itself (news_v2 auto-RAG +
        # live web search) and does NOT need bridge context — only the Zen/local
        # fallback path does. Awaiting the backend result FIRST means the common
        # path never serializes on the ~12s bridge web search. The context tasks
        # are only awaited when the backend returns None and the fallback path
        # actually needs them (and are cancelled when the backend answers).
        addresses = _extract_addresses(text)
        history = cast("list[dict[str, str]]", db.get_chat_history(user.id))

        backend_task = asyncio.create_task(chat_ai._call_backend_chat(text, user.id, history))
        daily_ctx_task = asyncio.create_task(get_daily_context())
        scan_ctx_task = asyncio.create_task(_fetch_scan_context(addresses[0])) if addresses else None
        bridge_ctx_task = asyncio.create_task(_fetch_bridge_context(text, history))
        # Bot-side live tools (REAL data): run concurrently with the backend so
        # the fallback path is grounded in real numbers and the guardrail can
        # verify the final answer. Bounded + fail-open — never blocks chat.
        live_tools_task = asyncio.create_task(chat_ai._run_live_tools(text, history))
        # Direct RAG knowledge lookup (knowledge questions only): searches the
        # intel DB BEFORE any web/LLM fallback. On the tier-1 path the backend
        # RAGs itself, so this task is cancelled when the backend answers.
        rag_task = asyncio.create_task(_run_rag_lookup(text)) if should_rag_lookup(text) else None

        # Tier-1: backend auto-RAG (POST /api/v1/chat, real X-RAG-Context count).
        # The backend injects the current date/time and runs its own RAG
        # (news_v2) + live web search, so it is already real-time aware.
        result = await backend_task

        # Bridge context/sources/actions only exist on the fallback path; the
        # backend answer carries its own grounding and never needs them.
        bridge_sources: list[dict[str, str]] = []
        bridge_actions: list[dict[str, str]] = []
        # Direct RAG lookup result — only set on the fallback path (backend path
        # cancels rag_task and keeps this None so the backend's own grounding
        # stays authoritative).
        rag_result: ToolResult | None = None
        # Whether the daily market/news digest or a bridge market block actually
        # reached the fallback prompt (feeds the truthful grounding footer).
        has_market_context = False

        if result is None:
            # Fallback path: gather the context fetches (fail-open) that ground
            # the Zen/local LLM calls, then build the message-specific prompt.
            result, bridge_sources, bridge_actions, rag_result, has_market_context = await _build_fallback_answer(
                text,
                user,
                history,
                daily_ctx_task=daily_ctx_task,
                scan_ctx_task=scan_ctx_task,
                bridge_ctx_task=bridge_ctx_task,
                live_tools_task=live_tools_task,
                rag_task=rag_task,
            )
        else:
            # Backend answered — the bridge/daily context fetches running in the
            # background are no longer needed. Cancel them so no coroutine dangles.
            # NOTE: live_tools_task is NOT cancelled — the guardrail below needs
            # its results to verify the backend answer (bounded; self-terminates).
            pending = [t for t in (daily_ctx_task, scan_ctx_task, bridge_ctx_task, rag_task) if t is not None]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        # Stop typing indicator + interim placeholder task
        typing_task.cancel()
        placeholder_task.cancel()
        await asyncio.gather(typing_task, placeholder_task, return_exceptions=True)

        # $TOKEN price card vs AI answer: the DexScreener lookup was fired
        # CONCURRENTLY at the top (ai_chat_handler) so the pipeline never
        # serialized on it. If the price card already landed it IS the answer —
        # skip the AI answer and clean up the placeholder. Otherwise cancel the
        # still-running lookup so a late price card never duplicates the answer.
        if await _token_card_won(token_lookup_task):
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.delete()
            return True
        await _cancel_token_lookup(token_lookup_task)

        # P2 race guard: the $TOKEN card can land just after the decide-wait
        # timed out (the shielded lookup keeps running for a beat after
        # _token_card_won returns, and _cancel_token_lookup can race it). If it
        # did, the card IS the answer — never double-send an AI reply.
        if token_card_flag is not None and token_card_flag.sent:
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.delete()
            return True

        if not result:
            if await _reply_local_intent(message, text, user):
                return True
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.edit_text(
                    _FALLBACK_MESSAGE,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
            return True

        answer = result["message"]

        # ── POST-PROCESSING FUNNEL ────────────────────────────────────────────
        # think-strip -> degenerate guard -> bridge citations -> zero-fabrication
        # guardrail -> honest no-live-data note -> last-chance degenerate guard.
        answer, live_results, stripped_syms, stripped_addrs, answer_is_degenerate = await _post_guardrail(
            answer,
            user_id=user.id,
            provider=str(result.get("provider") or ""),
            bridge_sources=bridge_sources,
            live_tools_task=live_tools_task,
            rag_result=rag_result,
        )

        # Last-chance P2 guard: a $TOKEN card can still land while the
        # guardrail finishes (it awaits live tools). The card wins — the AI
        # answer is skipped so the user never gets card + answer.
        if token_card_flag is not None and token_card_flag.sent:
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.delete()
            return True

        await _persist_answer(user.id, text, answer)

        await _send_and_trace_answer(
            message,
            preliminary,
            answer,
            user_id=user.id,
            text=text,
            pipeline_start=pipeline_start,
            result=result,
            bridge_sources=bridge_sources,
            bridge_actions=bridge_actions,
            live_results=live_results,
            rag_result=rag_result,
            has_market_context=has_market_context,
            answer_is_degenerate=answer_is_degenerate,
            stripped_syms=stripped_syms,
            stripped_addrs=stripped_addrs,
        )
        return True
    finally:
        # Cancel every task that is still running (done tasks are no-ops) so
        # no coroutine dangles on an early exception.
        pending_tasks = [
            t
            for t in (
                typing_task,
                placeholder_task,
                backend_task,
                daily_ctx_task,
                scan_ctx_task,
                bridge_ctx_task,
                live_tools_task,
                rag_task,
                token_lookup_task,
            )
            if t is not None and not t.done()
        ]
        for t in pending_tasks:
            t.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)


async def ai_chat_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Single free-text pipeline entry (MessageHandler).

    Merged from the old ``handlers.message.handle_message`` (owner admin
    console, bot kill-switch, known-scam block, bare-address auto-scan,
    $TOKEN price card) and the backend RAG chat core (:func:`chat_ai_response`).
    """
    user = update.effective_user
    message = update.message
    if user is None or message is None or not message.text:
        return
    text = message.text.strip()
    if not text:
        return

    u = db.get_or_create_user(user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return
    # NOTE: the per-user spam gate lives in chat_ai_response (single
    # check_spam call). Calling it HERE too was the P0 bug: the first call
    # STAMPS _user_last_action[user.id] = now (states.check_spam), then the
    # second call milliseconds later sees now - last < 3 → True → every
    # non-owner free-text reply was silently dropped.
    db.record_activity(msg=True)

    # Owner admin console (v7): in private chats the owner gets a persistent
    # admin reply keyboard + one-tap routing for its buttons. This runs
    # BEFORE the bot kill-switch so the owner can still reach /admin toggles
    # during maintenance.
    if is_owner(user.id) and update.effective_chat and update.effective_chat.type == "private":
        await _ensure_admin_reply_kb(update)
        if text in _ADMIN_BUTTON_MAP:
            await _dispatch_admin_button(update, ctx, text)
            return

    # Admin kill-switch: whole bot disabled (synced from the admin API).
    if db.get_bot_state("bot_enabled", "on") != "on":
        return

    # Analytics: count every inbound message handled (Redis, for the admin API).
    try:
        await track_message()
    except Exception:
        logger.debug("admin_state track_message failed", exc_info=True)

    # v6 anti-abuse: prompt injection + known scam addresses. Injection is
    # blocked BEFORE feature routing so an injected address is never scanned.
    injection = anti_abuse.check_prompt_injection(text)
    if injection:
        logger.info("prompt-injection blocked user=%s category=%s", user.id, injection)
        await _injection_warning(update, injection)
        return

    # $TOKEN price card: fire the DexScreener lookup CONCURRENTLY with the AI
    # pipeline instead of serializing a 10s HTTP call before it (the old
    # behavior delayed every "$TOKEN" message by up to 10s). The card wins if
    # it lands before the AI answer is ready (chat_ai_response decides);
    # structural routing cancels it when the message was already handled.
    # token_card_flag is the shared card-sent signal (P2 race fix) that lets
    # the AI pipeline detect a card landing just after the decide-wait and
    # never double-send card + AI answer.
    token_card_flag = _TokenCardFlag() if TOKEN_RE.search(text) else None
    token_lookup_task = (
        asyncio.create_task(_lookup_token_symbol(text, message, sent_flag=token_card_flag))
        if token_card_flag is not None
        else None
    )

    # Feature routing that does NOT consume the AI quota: known-scam block,
    # bare-address auto-scan. The $TOKEN lookup runs concurrently (above), so
    # _route_structural skips it here.
    if await _route_structural(update, ctx, text, user, skip_token_lookup=True):
        if token_lookup_task is not None:
            token_lookup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await token_lookup_task
        return

    # Free text → shared AI core (quota + flood + backend RAG chat).
    await chat_ai_response(
        update, ctx, text, user, token_lookup_task=token_lookup_task, token_card_flag=token_card_flag
    )


async def _send_answer(
    message: Message,
    answer: str,
    grounding_count: int,
    *,
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
    A short "⚡ Grounded in N intelligence sources | Rug Munch Bot" footer is
    appended to the last chunk when the answer had real grounding.
    ``grounding_count`` is the number of DISTINCT intelligence sources that
    actually fed the answer (RAG hits + live tools + web search + market
    context — see ``_grounding_sources_count``); counts above 100 are shown
    as "hundreds of sources".
    ``actions`` (bridge-suggested buttons) are rendered as an inline
    keyboard on the last chunk when present; otherwise the back menu is used.
    """
    raw = _beautify_answer(_tables_to_lists(strip_think_blocks(_normalize_llm_markup(answer))))
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
        if grounding_count > 100:
            source_label = "hundreds of intelligence sources"
        elif grounding_count == 1:
            source_label = "1 intelligence source"
        else:
            source_label = f"{grounding_count} intelligence sources"
        html[-1] = f"{html[-1]}\n\n<i>\u26a1 Grounded in {source_label} | Rug Munch Bot</i>"

    kb = _render_bridge_actions(actions) or back_kb()
    await send_in_parts(
        message,
        html,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    return len(html)


def register_chat_ai(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the free-text pipeline (MessageHandler) on the bot Application.

    One-line wiring for bot.py — this is the ONLY free-text handler:

        from rugmunchbot.handlers.chat_ai import register_chat_ai
        register_chat_ai(app)

    WIRING NOTE: python-telegram-bot dispatches to the FIRST matching handler,
    so no other ``MessageHandler(filters.TEXT & ~filters.COMMAND)`` may be
    registered alongside this one (the old ``handle_message`` registration
    was removed when the pipelines were merged).
    """
    from telegram.ext import MessageHandler, filters

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))


__all__ = [
    "SCAN_ENDPOINT",
    "SCAN_TIMEOUT",
    "TOKEN_CARD_DECIDE_WAIT",
    "_lookup_token_symbol",
    "_route_structural",
    "_send_answer",
    "_token_card_won",
    "ai_chat_handler",
    "chat_ai_response",
    "register_chat_ai",
]
