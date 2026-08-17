"""Chat routing — the shared AI core (``chat_ai_response``).

The whole merged turn pipeline: entry guards -> greeting/capability
instant replies -> backend RAG chat (tier-1) -> bridge/daily/scan/live
tools/rag/router context -> fallback orchestration -> post-guardrail ->
persist -> deliver, bounded by ONE hard end-to-end deadline. Patchable
seams (``chat_ai.check_spam``, ``chat_ai._call_backend_chat``,
``chat_ai._run_live_tools``) resolve through the chat_ai package.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from html import escape
from typing import TYPE_CHECKING, Any, cast

from telegram.constants import ParseMode
from telegram.error import TelegramError

import app.domains.telegram.rugmunchbot.handlers.chat_ai as chat_ai
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.daily_context import get_daily_context
from app.domains.telegram.rugmunchbot.db._base import run_db
from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing as _routing_mod
from app.domains.telegram.rugmunchbot.handlers.chat_ai._classify import (
    _extract_addresses,
    _is_capability_question,
    _is_greeting_or_thanks,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._delivery import (
    _edit_placeholder_if_slow,
    _keep_typing,
    _send_and_trace_answer,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._guards import (
    _post_guardrail,
    _reply_local_intent,
    _run_entry_guards,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._helpers import _FALLBACK_MESSAGE
from app.domains.telegram.rugmunchbot.handlers.chat_ai._live_tools import _run_rag_lookup
from app.domains.telegram.rugmunchbot.handlers.chat_ai._persistence import _persist_answer
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._constants import (
    CHAT_TURN_DEADLINE,
    CHAT_TURN_DEADLINE_LONG,
    TIMEOUT_REPLY,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._context import (
    _fetch_bridge_context,
    _fetch_scan_context,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._token_card import (
    _cancel_token_lookup,
    _token_card_won,
    _TokenCardFlag,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._trace import trace_answer
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.menus import capability_menu_kb
from app.domains.telegram.rugmunchbot.tools.rag_lookup import should_rag_lookup

if TYPE_CHECKING:
    from telegram import Message, Update
    from telegram.ext import ContextTypes

    from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")


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

    u = await run_db(db.get_or_create_user, user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return True
    if chat_ai.check_spam(user.id):
        return True

    # End-to-end answer latency is measured from here (all paths that send an
    # answer trace it via :func:`trace_answer`).
    pipeline_start = time.monotonic()

    # Task-lifecycle holders: the whole turn runs inside ONE try/except/
    # finally. The except is the fail-open boundary — an unexpected exception
    # in any single turn is logged, the placeholder becomes the honest canned
    # fallback, and the finally cancels every task that is still running.
    # The bot is a long-lived service: a turn that raises must NEVER take
    # down the process or leave a dangling "Thinking..." bubble / coroutine.
    typing_task: asyncio.Task[Any] | None = None
    placeholder_task: asyncio.Task[Any] | None = None
    preliminary: Message | None = None
    # P1 (task-lifecycle leak fix): every task created below must be
    # cancelled in the finally block — an early exception (backend RAG
    # failure, guardrail crash, send failure) must never leak a dangling
    # coroutine: the backend RAG call, the bridge/daily/scan context fetches,
    # the live tools, the direct RAG lookup and the caller-owned $TOKEN card
    # lookup (a late card firing on an exception path would be a stray
    # message). typing_task / placeholder_task are created below and are
    # cancelled too.
    backend_task: asyncio.Task[Any] | None = None
    daily_ctx_task: asyncio.Task[Any] | None = None
    scan_ctx_task: asyncio.Task[Any] | None = None
    bridge_ctx_task: asyncio.Task[Any] | None = None
    live_tools_task: asyncio.Task[Any] | None = None
    rag_task: asyncio.Task[Any] | None = None
    router_ctx_task: asyncio.Task[Any] | None = None

    # The whole turn (guards -> backend RAG -> local LLM fallback -> live
    # tools -> guardrail -> send) runs as ONE nested coroutine so the caller
    # below can bound it with a single asyncio.wait_for deadline. nonlocal
    # keeps the task-lifecycle holders (created above) visible to the
    # enclosing fail-open except/finally handlers.
    async def _generate_response() -> None:
        """Run one full chat turn; returns after the answer is sent (or handled).

        Nested so :func:`chat_ai_response` can bound the entire turn with a
        single ``asyncio.wait_for(turn_deadline)`` — the inner stage
        budgets (backend 60s, local LLM 60s, tools ~8s+5s) can stack to
        ~121s without it. The deadline is tier-aware: snappy/backend turns
        get ``CHAT_TURN_DEADLINE`` (25s), long-tier deep turns get
        ``CHAT_TURN_DEADLINE_LONG`` (34s) so the hard-capped context gather
        + 15s long-model call + possible paid fallback fit. Cancellation
        propagates into the inner stages, so the outer deadline supersedes
        them.
        """
        nonlocal typing_task, placeholder_task, preliminary, backend_task
        nonlocal daily_ctx_task, scan_ctx_task, bridge_ctx_task, live_tools_task, rag_task, router_ctx_task
        # Pre-LLM guards: chat_enabled toggle -> injection -> quota -> flood ->
        # gibberish (tier-3 redirect). True = message already handled.
        if await _run_entry_guards(update, message, user, text, pipeline_start=pipeline_start):
            await _cancel_token_lookup(token_lookup_task)
            return

        # Tier-2: ONLY instant-reply for greetings, thanks, and capability inquiries.
        # Everything else goes to the LLM — the bot should answer what the user asks.
        # Capability questions ("what can you do?") attach the capability menu so
        # the bot's surface is discoverable right from the first answer.
        capability_menu = capability_menu_kb() if _is_capability_question(text) else None
        if (_is_greeting_or_thanks(text) or _is_capability_question(text)) and await _reply_local_intent(
            message, text, user, menu_kb=capability_menu
        ):
            await _cancel_token_lookup(token_lookup_task)
            return

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
        # Narrowed here: after a successful send the placeholder always exists
        # for the rest of the turn (the except handler guards the None case).
        assert preliminary is not None

        # Interim "still digging" note: fired ~5s after the placeholder so a slow
        # backend query doesn't look stalled. Cancelled the moment the answer is
        # ready (below), so a fast response never gets clobbered by the edit.
        placeholder_task = asyncio.create_task(_edit_placeholder_if_slow(preliminary))

        # CONCURRENT: fire the backend RAG chat (tier-1) AND the fallback context
        # fetches at the same time. The backend RAGs itself (news_v2 auto-RAG +
        # live web search) and does NOT need bridge context — only the local
        # fallback path does. Awaiting the backend result FIRST means the common
        # path never serializes on the ~12s bridge web search. The context tasks
        # are only awaited when the backend returns None and the fallback path
        # actually needs them (and are cancelled when the backend answers).
        addresses = _extract_addresses(text)
        history = cast("list[dict[str, str]]", await run_db(db.get_chat_history, user.id))

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
        # Question-type router context (fail-open, bounded ~5s internally):
        # fired CONCURRENTLY with the backend so a fallback turn never waits
        # for it serially (the old path started it only after the backend
        # returned None — up to 5s of pure serial latency). Cancelled when the
        # backend answers (tier-1 RAGs itself and never needs it).
        router_ctx_task = asyncio.create_task(_routing_mod._gather_context(text))

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
            # the local LLM calls, then build the message-specific prompt.
            (
                result,
                bridge_sources,
                bridge_actions,
                rag_result,
                has_market_context,
            ) = await _routing_mod._build_fallback_answer(
                text,
                user,
                history,
                daily_ctx_task=daily_ctx_task,
                scan_ctx_task=scan_ctx_task,
                bridge_ctx_task=bridge_ctx_task,
                live_tools_task=live_tools_task,
                rag_task=rag_task,
                router_ctx_task=router_ctx_task,
            )
        else:
            # Backend answered — the bridge/daily context fetches running in the
            # background are no longer needed. Cancel them so no coroutine dangles.
            # NOTE: live_tools_task is NOT cancelled — the guardrail below needs
            # its results to verify the backend answer (bounded; self-terminates).
            pending = [
                t for t in (daily_ctx_task, scan_ctx_task, bridge_ctx_task, rag_task, router_ctx_task) if t is not None
            ]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        # The interim "still digging" edit task has served its purpose — cancel
        # it so it can never clobber the answer send. The typing indicator
        # STAYS alive here: the $TOKEN decide-wait, the post-processing
        # guardrail and the DB persist can still take a second, and the bubble
        # must not drop before the answer actually lands. It is cancelled
        # immediately before _send_and_trace_answer below; the finally block is
        # the backstop for every early-return path.
        if placeholder_task is not None:
            placeholder_task.cancel()
            await asyncio.gather(placeholder_task, return_exceptions=True)

        # $TOKEN price card vs AI answer: the DexScreener lookup was fired
        # CONCURRENTLY at the top (ai_chat_handler) so the pipeline never
        # serialized on it. If the price card already landed it IS the answer —
        # skip the AI answer and clean up the placeholder. Otherwise cancel the
        # still-running lookup so a late price card never duplicates the answer.
        if await _token_card_won(token_lookup_task):
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.delete()
            return
        await _cancel_token_lookup(token_lookup_task)

        # P2 race guard: the $TOKEN card can land just after the decide-wait
        # timed out (the shielded lookup keeps running for a beat after
        # _token_card_won returns, and _cancel_token_lookup can race it). If it
        # did, the card IS the answer — never double-send an AI reply.
        if token_card_flag is not None and token_card_flag.sent:
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.delete()
            return

        if not result:
            if await _reply_local_intent(message, text, user):
                return
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.edit_text(
                    _FALLBACK_MESSAGE,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
            # Per-turn error visibility: the whole synthesis chain failed
            # (backend + local + tools) — emit an explicit error trace so the
            # outage is greppable with latency, not just a placeholder edit.
            trace_answer(
                question=text,
                user_id=user.id,
                intent="chat",
                route="error",
                ok=False,
                latency_ms=(time.monotonic() - pipeline_start) * 1000,
            )
            return

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
            return

        await _persist_answer(user.id, text, answer)

        # The answer is about to be sent — drop the typing bubble NOW so the
        # user sees the bubble disappear and the reply land in the same beat
        # (Telegram clears the bubble server-side once a message arrives, but
        # cancelling here keeps us from re-firing a stale pulse mid-send).
        if typing_task is not None:
            typing_task.cancel()
            await asyncio.gather(typing_task, return_exceptions=True)

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
        return

    try:
        # ONE hard end-to-end deadline around the whole turn: whatever stage
        # is in flight (backend RAG / local LLM / tools / guardrail / send),
        # the user gets a reply within the deadline. wait_for cancels the
        # pipeline on timeout and the handler below replies gracefully —
        # this never surfaces to the PTB error handler as a crash.
        #
        # Tier-aware: deep-analysis questions (routed to the "long" provider
        # by _route_tier) get CHAT_TURN_DEADLINE_LONG so the hard-capped
        # context gather + 15s long-model call + possible paid fallback fit;
        # snappy/backend turns keep the fast CHAT_TURN_DEADLINE. The same
        # deterministic _route_tier(text) the fallback chain uses, so the
        # deadline always matches the tier that actually answers.
        turn_deadline = CHAT_TURN_DEADLINE_LONG if _routing_mod._route_tier(text) == "long" else CHAT_TURN_DEADLINE
        await asyncio.wait_for(_generate_response(), timeout=turn_deadline)
        return True
    except TimeoutError:
        # Hard deadline breached: cancel the typing/placeholder upkeep so no
        # stale "still digging" edit or typing pulse fires late, then swap
        # the placeholder for a short graceful reply and trace the timeout.
        # The finally below cancels every still-running pipeline task.
        if typing_task is not None:
            typing_task.cancel()
        if placeholder_task is not None:
            placeholder_task.cancel()
        await asyncio.gather(
            *(t for t in (typing_task, placeholder_task) if t is not None),
            return_exceptions=True,
        )
        trace_answer(
            question=text,
            user_id=user.id,
            intent="chat",
            route="timeout",
            ok=False,
            latency_ms=(time.monotonic() - pipeline_start) * 1000,
        )
        if preliminary is not None:
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.edit_text(
                    TIMEOUT_REPLY,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
        else:
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await message.reply_text(
                    TIMEOUT_REPLY,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
        return True
    except Exception as exc:
        # ── FAIL-OPEN BOUNDARY (single-turn isolation) ─────────────────────
        # An unexpected exception in ONE turn must never kill the bot process
        # or the event loop (the bot is a long-lived service), and must never
        # leave the user with a dangling "Thinking..." bubble. Log it with
        # stack, emit an error trace (latency + ok=False), swap the
        # placeholder for the honest canned fallback, and let the finally
        # cancel every still-running task. CancelledError is a BaseException
        # and is NOT caught here — a shutdown-cancelled pipeline unwinds.
        logger.exception("chat_ai pipeline error (user=%s): %s", user.id, exc)
        trace_answer(
            question=text,
            user_id=user.id,
            intent="chat",
            route="error",
            ok=False,
            latency_ms=(time.monotonic() - pipeline_start) * 1000,
        )
        fallback_text = _FALLBACK_MESSAGE
        if preliminary is not None:
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await preliminary.edit_text(
                    fallback_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                )
        else:
            with contextlib.suppress(TelegramError, ConnectionError, OSError, TimeoutError):
                await message.reply_text(
                    fallback_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
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
                router_ctx_task,
                token_lookup_task,
            )
            if t is not None and not t.done()
        ]
        for t in pending_tasks:
            t.cancel()
        await asyncio.gather(*pending_tasks, return_exceptions=True)
