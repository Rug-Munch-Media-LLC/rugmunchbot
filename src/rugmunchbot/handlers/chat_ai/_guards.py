"""Entry guards + answer-quality guardrails for the free-text AI chat pipeline.

Extracted from ``chat_ai/__init__.py`` during the de-godding pass: the
anti-abuse warnings (prompt-injection / known-scam), the tier-2/tier-3
fallback replies, the pre-LLM entry guards (chat_enabled toggle → injection →
weekly quota → flood → gibberish) and the post-processing funnel every
provider's answer flows through (think-strip → degenerate guard → bridge
citations → zero-fabrication guardrail → honest no-live-data note).

MONKEYPATCH CONTRACT: the pipeline tests replace ``chat_ai.check_flood`` and
``chat_ai.refund_flood`` on the package, so those two lookups go through the
package module at call time instead of a module-level binding.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

import rugmunchbot.handlers.chat_ai as chat_ai
from rugmunchbot import anti_abuse, db
from rugmunchbot._vendor.ai_guardrails import sanitize_output
from rugmunchbot.admin_state import track_ai_query, track_tokens
from rugmunchbot.db._base import run_db
from rugmunchbot.formatters import paywall_text
from rugmunchbot.handlers.chat_ai._classify import _is_meaningful_question
from rugmunchbot.handlers.chat_ai._fabrication_guard import apply_fabrication_guardrail
from rugmunchbot.handlers.chat_ai._helpers import (
    _CHAT_DISABLED,
    _DEGENERATE_MESSAGE,
    _INJECTION_MESSAGES,
    _KNOWN_SCAM_WARNING,
    _REDIRECT_MESSAGE,
    _SLOW_DOWN,
)
from rugmunchbot.handlers.chat_ai._live_tools import _run_live_tools_bounded
from rugmunchbot.handlers.chat_ai._local_ai import local_ai_response
from rugmunchbot.handlers.chat_ai._quality import is_degenerate_answer, strip_think_blocks
from rugmunchbot.handlers.chat_ai._trace import trace_answer
from rugmunchbot.keyboards import back_kb, main_menu_kb, paywall_kb
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    import asyncio

    from telegram import Message, Update

    from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")


_LLMGUARD_URL = "http://127.0.0.1:8016"


async def _ml_guard_check(text: str) -> str | None:
    """ML second stage (llm-guard sidecar @ :8016) — catches paraphrased attacks.

    Fail-open: any sidecar error/timeout returns None and the regex layer
    remains the only guard (availability over paranoia).
    """
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=1.5, read=8.0, write=3.0, pool=5.0)
        ) as client:
            resp = await client.post(
                f"{_LLMGUARD_URL}/scan", json={"text": text[:6000], "kind": "input"}
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("safe", True):
                    return str(data.get("flag") or "flagged")
    except Exception as exc:  # fail-open by design
        logger.warning("llm-guard sidecar unavailable (%s)", str(exc)[:100])
    return None


async def _ml_guard_output_scrub(text: str) -> str:
    """ML output redaction via the sidecar. Fail-open (returns input unchanged)."""
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=1.5, read=8.0, write=3.0, pool=5.0)
        ) as client:
            resp = await client.post(
                f"{_LLMGUARD_URL}/scan", json={"text": text[:8000], "kind": "output"}
            )
            if resp.status_code == 200:
                data = resp.json()
                sanitized = data.get("sanitized")
                if isinstance(sanitized, str):
                    return sanitized
    except Exception as exc:  # fail-open by design
        logger.warning("llm-guard output unavailable (%s)", str(exc)[:100])
    return text


async def _injection_warning(update: Update, category: str) -> None:
    """Reply to a prompt-injection attempt with a targeted warning."""
    text = _INJECTION_MESSAGES.get(
        category, "I couldn't process that message. Ask me a normal question instead."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\U0001f50d /scan — Audit a token", callback_data="run_scan")],
            [InlineKeyboardButton("\U0001f4ac /chat — Ask me anything", callback_data="run_chat")],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )
    try:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\U0001f6e1\ufe0f <b>Rug Munch Anti-Abuse</b>\n\n{text}\n\n<i>Message blocked: {category}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("injection warning send failed", exc_info=True)


async def _scam_address_warning(update: Update, address: str) -> None:
    """Reply when the user shares a known scam address."""
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f50d /report — Report a scam", callback_data="run_report"
                )
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )
    try:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"{_KNOWN_SCAM_WARNING}\n\n"
            f"\U0001f517 Address: <code>{address}</code>\n\n"
            f"Already flagged one like this? /report helps us build the denylist.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("scam address warning send failed", exc_info=True)


# ══════════════════════════════════════════════
# SHARED AI CORE (free text tail + /chat + /ask)
# ══════════════════════════════════════════════


async def _reply_local_intent(
    message: Message,
    text: str,
    user: Any,
    *,
    menu_kb: InlineKeyboardMarkup | None = None,
) -> bool:
    """Tier-2 canned reply (no backend needed).

    Used for gibberish that is not worth an LLM call and as an offline
    fallback when the backend chat is unavailable. Returns True when a
    canned answer was sent. ``menu_kb`` optionally replaces the default back
    keyboard (e.g. the capability menu for "what can you do" questions).
    """
    local_response = local_ai_response(text, user)
    if not local_response:
        return False
    t0 = time.monotonic()
    logger.info("AI chat tier=2 (local intent) user=%s text=%.60s", user.id, text)
    await run_db(db.increment_usage, user.id, ai_msg=True)
    try:
        await track_ai_query()
        await track_tokens(len(local_response) // 4)
    except Exception:
        logger.debug("chat_ai analytics failed (user=%s)", user.id, exc_info=True)
    try:
        await message.reply_text(
            local_response,
            parse_mode=ParseMode.HTML,
            reply_markup=menu_kb or back_kb(),
            disable_web_page_preview=True,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("chat_ai local intent reply failed", exc_info=True)
        return False
    trace_answer(
        question=text,
        user_id=user.id,
        intent="chat",
        route="local_intent",
        latency_ms=(time.monotonic() - t0) * 1000,
        ok=True,
    )
    return True


async def _reply_redirect(message: Message, user: Any, flood_receipt: str | None) -> None:
    """Tier-3 catch-all redirect (NOT counted against flood limits)."""
    logger.info("AI chat tier=3 (redirect) user=%s", user.id)
    try:
        await chat_ai.refund_flood(user.id, flood_receipt)
    except Exception:
        logger.debug("refund_flood failed (user=%s)", user.id, exc_info=True)
    await run_db(db.increment_usage, user.id, ai_msg=True)
    try:
        await track_ai_query()
        await track_tokens(len(_REDIRECT_MESSAGE) // 4)
    except Exception:
        logger.debug("chat_ai analytics failed (user=%s)", user.id, exc_info=True)
    try:
        await message.reply_text(
            _REDIRECT_MESSAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(user.id),
            disable_web_page_preview=True,
        )
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("chat_ai redirect reply failed", exc_info=True)


async def _run_entry_guards(
    update: Update,
    message: Message,
    user: Any,
    text: str,
    *,
    pipeline_start: float,
) -> bool:
    """Run the pre-LLM chat guards; True when the message was handled.

    chat_enabled toggle → prompt-injection block → weekly AI quota →
    per-user flood limit → gibberish gate (tier-3 redirect, flood-refunded).
    Every branch sends the reply itself (the redirect also traces its
    answer), so the caller simply stops when this returns True.
    """
    # Owner mode: the boss bypasses every entry guard (trusted principal).
    try:
        from rugmunchbot.states import is_owner

        if is_owner(user.id):
            logger.info("owner mode: entry guards bypassed (user=%s)", user.id)
            return False
    except Exception as exc:
        logger.warning("owner-mode check failed (treating as non-owner): %s", str(exc)[:120])

    if await run_db(db.get_bot_state, "chat_enabled", "on") != "on":
        await message.reply_text(
            _CHAT_DISABLED, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(user.id)
        )
        return True

    # v6 anti-abuse: never forward prompt-injection attempts to the LLM.
    injection = anti_abuse.check_prompt_injection(text)
    if not injection:
        # v7: ML second stage — catches paraphrased attacks the regex misses.
        _ml_flag = await _ml_guard_check(text)
        if _ml_flag:
            injection = f"ml guard: {_ml_flag}"
    if injection:
        logger.info("prompt-injection blocked (chat_ai) user=%s category=%s", user.id, injection)
        await _injection_warning(update, injection)
        return True

    # Weekly AI-message quota per tier (free 15 / scout 75 / hunter 250 / pro 500).
    allowed, _used, _limit = await run_db(db.check_rate_limit, user.id, "ai_msg")
    if not allowed:
        await message.reply_text(
            paywall_text(user.id, "ai_msg"),
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return True

    # Per-user flood limit (3/s, 30/min) — same surface as the rest of AI chat.
    flood_ok, retry_after, flood_receipt = await chat_ai.check_flood(user.id)
    if not flood_ok:
        await message.reply_text(
            _SLOW_DOWN.format(retry=retry_after),
            parse_mode=ParseMode.HTML,
        )
        return True

    # Gibberish gate — don't burn LLM tokens on noise.
    if not _is_meaningful_question(text):
        await _reply_redirect(message, user, flood_receipt)
        trace_answer(
            question=text,
            user_id=user.id,
            intent="chat",
            route="redirect",
            latency_ms=(time.monotonic() - pipeline_start) * 1000,
            ok=True,
        )
        return True

    return False


@dataclass
class GuardrailContext:
    user_id: int
    provider: str
    bridge_sources: list[dict[str, str]]
    live_tools_task: asyncio.Task[dict[str, ToolResult]]
    rag_result: ToolResult | None


async def _post_guardrail(
    answer: str, gctx: GuardrailContext
) -> tuple[str, dict[str, ToolResult], list[str], list[str], bool]:
    """Post-processing funnel every provider's answer flows through.

    Request fields live on ``gctx`` (a :class:`GuardrailContext`).

    Think-strip → degenerate guard → zero-fabrication guardrail (symbols,
    addresses, numbers — see ``_fabrication_guard``) → honest no-live-data
    note → last-chance degenerate guard → final output scrub. Returns
    ``(answer, live_results, stripped_syms, stripped_addrs,
    answer_is_degenerate)`` — the extras feed the grounding footer and the
    structured answer trace.
    """
    # Single funnel every provider's answer flows through, so the user, the
    # stored history and the guardrails never see a <think> chain.
    answer = strip_think_blocks(answer)

    user_id = gctx.user_id
    provider = gctx.provider
    live_tools_task = gctx.live_tools_task
    rag_result = gctx.rag_result
    answer_is_degenerate = is_degenerate_answer(answer)
    if answer_is_degenerate:
        logger.warning(
            "AI chat degenerate answer replaced (user=%s provider=%s)", user_id, provider
        )
        answer = _DEGENERATE_MESSAGE

    # Re-join the live-tools task with a short budget: they started at the top
    # of the pipeline, so most are already done and wait_for returns instantly.
    live_results = await _run_live_tools_bounded(live_tools_task, 0.3, reuse_done=True)
    if rag_result is not None and rag_result.ok:
        # Fallback path only: the direct RAG lookup is real grounding.
        live_results["rag_lookup"] = rag_result
    answer, stripped_syms, stripped_addrs = apply_fabrication_guardrail(
        answer, user_id, live_results, provider == "tools"
    )

    if is_degenerate_answer(answer):
        # The guardrail can strip a short answer down to nothing — never send
        # an empty or broken reply.
        logger.warning("AI chat answer degenerated after guardrail (user=%s)", user_id)
        answer = _DEGENERATE_MESSAGE
        answer_is_degenerate = True

    # Final scrub, deliberately last: no internal identifier reaches Telegram.
    answer = sanitize_output(answer)
    answer = await _ml_guard_output_scrub(answer)

    return answer, live_results, stripped_syms, stripped_addrs, answer_is_degenerate


__all__ = [
    "_injection_warning",
    "_post_guardrail",
    "_reply_local_intent",
    "_reply_redirect",
    "_run_entry_guards",
    "_scam_address_warning",
]
