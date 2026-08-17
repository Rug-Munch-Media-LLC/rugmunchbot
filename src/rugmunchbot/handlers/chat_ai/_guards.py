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

import bisect
import math
import re
import time
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

import app.domains.telegram.rugmunchbot.handlers.chat_ai as chat_ai
from app.core.ai_guardrails import sanitize_output
from app.domains.telegram.rugmunchbot import anti_abuse, db
from app.domains.telegram.rugmunchbot.admin_state import track_ai_query, track_tokens
from app.domains.telegram.rugmunchbot.db._base import run_db
from app.domains.telegram.rugmunchbot.formatters import paywall_text
from app.domains.telegram.rugmunchbot.handlers.chat_ai._classify import _is_meaningful_question
from app.domains.telegram.rugmunchbot.handlers.chat_ai._helpers import (
    _CHAT_DISABLED,
    _DEGENERATE_MESSAGE,
    _INJECTION_MESSAGES,
    _KNOWN_SCAM_WARNING,
    _REDIRECT_MESSAGE,
    _SLOW_DOWN,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._live_tools import _run_live_tools_bounded
from app.domains.telegram.rugmunchbot.handlers.chat_ai._local_ai import local_ai_response
from app.domains.telegram.rugmunchbot.handlers.chat_ai._quality import is_degenerate_answer, strip_think_blocks
from app.domains.telegram.rugmunchbot.handlers.chat_ai._trace import trace_answer
from app.domains.telegram.rugmunchbot.keyboards import back_kb, main_menu_kb, paywall_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools import known_addresses, known_symbols
from app.domains.telegram.rugmunchbot.tools.guardrails import NO_LIVE_DATA_MESSAGE, fabrication_note, sanitize_answer

if TYPE_CHECKING:
    import asyncio

    from telegram import Message, Update

    from app.domains.telegram.rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.chat_ai")


# ══════════════════════════════════════════════
# NUMERIC-VERIFICATION (zero-fabrication, numbers layer)
# ══════════════════════════════════════════════
# The symbol/address guardrail (tools/guardrails.py::sanitize_answer) has no
# way to catch a hallucinated NUMBER — "$420.69" or "+999%" carry no ticker
# to strip. These helpers add the numbers layer: every ``$amount`` / ``±%``
# claim in the answer is cross-checked (cheap regex + tolerant membership)
# against the real numbers the live tools returned (market_overview /
# price_lookup / scanner / rag_lookup payloads). A claim matching no fetched
# datum is flagged inline — visibly, never silently stripped.

# USD amounts: "$420.69", "$12,345", "$2.5M", "$50 million". A "$" followed
# by a DIGIT — a "$" followed by LETTERS is a ticker (TOKEN_RE in
# tools/guardrails.py) and is deliberately NOT matched here.
_USD_CLAIM_RE = re.compile(
    r"\$(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s*(K|M|B|million|billion|trillion))?\b",
    re.IGNORECASE,
)
# Signed percentage moves: "+2.3%", "-12.5%". Unsigned percentages in prose
# ("42% of supply") are not market claims of the +X%/-X% shape this pass
# targets, so they are deliberately left alone.
_PERCENT_CLAIM_RE = re.compile(r"([+-]\d+(?:\.\d+)?)\s*%")
# Human-readable unit suffixes on $amount claims.
_CLAIM_SUFFIX_MULT = {"k": 1e3, "m": 1e6, "b": 1e9, "million": 1e6, "billion": 1e9, "trillion": 1e12}
# Tolerance rules: a claim is verified when some fetched datum lies within
# REL_TOL relative (2%) — or ABS_FLOOR absolute (sub-cent prices) — of it.
REL_TOL = 0.02
ABS_FLOOR = 1e-9


def _claim_value(raw: str, suffix: str | None) -> float | None:
    """Parse a captured claim's number (+ optional K/M/B/million suffix)."""
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        value *= _CLAIM_SUFFIX_MULT.get(suffix.lower(), 1.0)
    return value


def _walk_numeric(node: Any, out: list[float]) -> None:
    """Collect every finite numeric leaf of a tool payload (dict/list/str)."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        if math.isfinite(node):
            out.append(float(node))
        return
    if isinstance(node, str):
        # Numeric strings ("0.00001234") are real data (DexScreener etc.).
        try:
            value = float(node.replace(",", ""))
        except ValueError:
            return
        if math.isfinite(value):
            out.append(value)
        return
    if isinstance(node, dict):
        for child in node.values():
            _walk_numeric(child, out)
    elif isinstance(node, (list, tuple)):
        for child in node:
            _walk_numeric(child, out)


def _collect_numeric_facts(live_results: dict[str, ToolResult]) -> list[float]:
    """Every real number the tools returned — the datum set to verify against.

    Skips failed tools and web_search knowledge fallbacks (synthesized text,
    not corroborating live data — mirrors the ``tools_ok`` gate in
    ``_post_guardrail``).
    """
    out: list[float] = []
    for result in live_results.values():
        if not result.ok or (result.tool == "web_search" and str(result.source).startswith("knowledge")):
            continue
        if result.data is not None:
            _walk_numeric(result.data, out)
    return out


def _within_tolerance(claim: float, known: list[float]) -> bool:
    """Tolerant set-membership: is ``claim`` within tolerance of a datum?

    ``known`` must be a sorted, deduplicated list of fetched numbers.
    Bisection checks only the two nearest neighbors — O(log n) per claim,
    no API calls, fully deterministic.
    """
    index = bisect.bisect_left(known, claim)
    for datum in (known[index - 1] if index > 0 else None, known[index] if index < len(known) else None):
        if datum is not None and abs(claim - datum) <= max(REL_TOL * abs(datum), ABS_FLOOR):
            return True
    return False


def _verify_numeric_claims(answer: str, known_nums: list[float]) -> tuple[str, list[str]]:
    """Flag inline every $amount / ±% claim that matches no fetched datum.

    Verified claims (a fetched number within tolerance) are left untouched;
    unmatched claims get ``" ⚠ unverified"`` appended after the number.
    Returns ``(flagged_answer, flagged_claims)``. With an empty datum set the
    answer is returned unchanged — no data means nothing to verify against
    (the NO_LIVE_DATA path handles that).
    """
    known = sorted({k for k in known_nums if math.isfinite(k)})
    if not known:
        return answer, []
    flagged: list[str] = []

    def _flag(match: re.Match[str], value: float | None) -> str:
        if value is None:
            return match.group(0)
        # Idempotency guard: never double-flag an already-flagged claim.
        if answer[match.end() :].lstrip().startswith("⚠ unverified"):
            return match.group(0)
        if _within_tolerance(value, known):
            return match.group(0)
        flagged.append(match.group(0))
        return f"{match.group(0)} ⚠ unverified"

    answer = _USD_CLAIM_RE.sub(lambda m: _flag(m, _claim_value(m.group(1), m.group(2))), answer)
    answer = _PERCENT_CLAIM_RE.sub(lambda m: _flag(m, _claim_value(m.group(1), None)), answer)
    return answer, flagged


async def _injection_warning(update: Update, category: str) -> None:
    """Reply to a prompt-injection attempt with a targeted warning."""
    text = _INJECTION_MESSAGES.get(category, "I couldn't process that message. Ask me a normal question instead.")
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
            [InlineKeyboardButton("\U0001f50d /report — Report a scam", callback_data="run_report")],
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
    if await run_db(db.get_bot_state, "chat_enabled", "on") != "on":
        await message.reply_text(_CHAT_DISABLED, parse_mode=ParseMode.HTML, reply_markup=main_menu_kb(user.id))
        return True

    # v6 anti-abuse: never forward prompt-injection attempts to the LLM.
    injection = anti_abuse.check_prompt_injection(text)
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


async def _post_guardrail(
    answer: str,
    *,
    user_id: int,
    provider: str,
    bridge_sources: list[dict[str, str]],
    live_tools_task: asyncio.Task[dict[str, ToolResult]],
    rag_result: ToolResult | None,
) -> tuple[str, dict[str, ToolResult], list[str], list[str], bool]:
    """Post-processing funnel every provider's answer flows through.

    Think-strip → degenerate guard → bridge citations (local) →
    zero-fabrication guardrail → honest no-live-data note → last-chance
    degenerate guard. Returns ``(answer, live_results, stripped_syms,
    stripped_addrs, answer_is_degenerate)`` — the extras feed the grounding
    footer and the structured answer trace.
    """
    # ── THINK-STRIPPING (post-processing) ─────────────────────────────────
    # Some providers emit visible <think>...</think>
    # reasoning chains; the backend / remote providers can all surface
    # them. Strip here — the single funnel every provider's answer flows
    # through — so the user, the stored chat history, and the fabrication
    # guardrail never see the chain. Handles complete + unterminated blocks
    # (single shared implementation — see app.core.ai_guardrails.strip_think_tags).
    answer = strip_think_blocks(answer)

    # ── DEGENERATE-OUTPUT GUARD (post-processing) ─────────────────────────
    # A model can degenerate into a bare "-", punctuation-only output,
    # or a token-repetition loop ("ioneerioneerioneer..."). Never surface
    # that — replace it with the helpful fallback message. Runs BEFORE the
    # fabrication guardrail: garbage carries no symbols worth verifying.
    # (``answer_is_degenerate`` also suppresses the grounding footer below.)
    answer_is_degenerate = is_degenerate_answer(answer)
    if answer_is_degenerate:
        logger.warning("AI chat degenerate answer replaced (user=%s provider=%s)", user_id, provider)
        answer = _DEGENERATE_MESSAGE

    # (The old ``provider == "local"`` bridge-citation branch was removed —
    # local models were decommissioned 2026-08-16 and no pipeline provider
    # carries that value anymore.)

    # ── ZERO-FABRICATION GUARDRAIL (post-processing) ────────────────────
    # Strip/reject any token symbol or address the bot did NOT receive from
    # a tool call. When no tool matched the question (general knowledge),
    # there is nothing to verify against and the backend's own grounding
    # rules apply — nothing is stripped.
    #
    # Backend path: the backend answer is grounded by ITS OWN tool layer +
    # RAG, so we only strip when our bot-side tools independently returned
    # data for this question (corroboration). If our tools all failed, the
    # backend's grounding is authoritative.
    #
    # Fallback path (tools-only answers): bot-side tools are the ONLY
    # real-data grounding, so the guardrail is strict — strip everything not
    # returned by a tool, and say "no live data" when every tool failed.
    # Re-join the live-tools task for the guardrail WITHOUT stalling the
    # answer. The tools were started at the top of the pipeline, so by the
    # time the answer is ready most are done (wait_for returns instantly for
    # a finished task). A ~0.3s budget catches late arrivals; the old
    # hardcoded 2.0s re-join added a full 2s to every answer's latency.
    live_results = await _run_live_tools_bounded(live_tools_task, 0.3, reuse_done=True)
    # On the fallback path the direct RAG lookup is REAL grounding — merge it
    # so its content symbols/addresses protect RAG-cited tickers and its
    # success counts as corroboration. Never merged on the backend path
    # (rag_result stays None there; the backend's own grounding is
    # authoritative).
    if rag_result is not None and rag_result.ok:
        live_results["rag_lookup"] = rag_result
    stripped_syms: list[str] = []
    stripped_addrs: list[str] = []
    was_fallback = provider == "tools"
    if live_results:
        # web_search's synthesized fallbacks (knowledge answer / honest
        # no-live-data) are NOT corroborating live data — they carry no
        # tool-verified symbols, so counting them would flip tools_ok and
        # make the guardrail strip every ticker from a backend-grounded
        # answer (empty known set). They ARE usable answers, so they still
        # suppress the no-live-data note below.
        tools_ok = any(
            r.ok and not (r.tool == "web_search" and str(r.source).startswith("knowledge"))
            for r in live_results.values()
        )
        if was_fallback or tools_ok:
            known_syms = known_symbols(live_results)
            known_addrs = known_addresses(live_results)
            answer, stripped_syms, stripped_addrs = sanitize_answer(answer, known_syms, known_addrs)
            if stripped_syms or stripped_addrs:
                note = fabrication_note(stripped_syms, stripped_addrs)
                if note:
                    answer = f"{answer}{note}"
                    logger.info(
                        "guardrail stripped fabricated data user=%s syms=%s addrs=%s",
                        user_id,
                        stripped_syms[:5],
                        stripped_addrs[:2],
                    )
            # ── NUMERIC-VERIFICATION PASS ─────────────────────────────
            # The symbol/address guardrail above cannot catch hallucinated
            # NUMBERS ("$420.69", "+999%") — there is no ticker to strip.
            # Cross-check every $amount / ±% claim against the numbers the
            # tools ACTUALLY returned (market_overview / price_lookup /
            # scanner / rag payloads). A claim matching no fetched datum
            # (within tolerance) is flagged inline — visibly, not silently
            # stripped. Skipped entirely when the tools returned no numeric
            # data: with nothing to compare against, the existing no-live-data
            # / backend-grounding paths stay authoritative.
            known_nums = _collect_numeric_facts(live_results)
            if known_nums:
                answer, flagged_nums = _verify_numeric_claims(answer, known_nums)
                if flagged_nums:
                    logger.info(
                        "guardrail flagged unverified numbers user=%s nums=%s",
                        user_id,
                        flagged_nums[:5],
                    )
        # Honest no-live-data signal (FALLBACK path only): the question routed
        # to tools but every source was down/rate-limited → the bot must not
        # guess. The backend path has its own grounding + its own tool layer,
        # so appending this there would be a false "no data". Suppressed when
        # ANY tool produced a usable answer — including a web_search
        # knowledge/no-live-data fallback (the honest message already lives
        # inside that result, and the answer is not a dead end).
        if was_fallback and not any(r.ok for r in live_results.values()):
            answer = f"{answer}\n\n{NO_LIVE_DATA_MESSAGE}"

    # Last-chance degenerate guard: the fabrication guardrail can strip a
    # short answer down to nothing (e.g. an answer that was only "$FAKE").
    # Never send an empty/broken reply — replace with the helpful fallback.
    if is_degenerate_answer(answer):
        logger.warning("AI chat answer degenerated after guardrail (user=%s)", user_id)
        answer = _DEGENERATE_MESSAGE
        answer_is_degenerate = True

    # ── OUTPUT SANITIZATION (final scrub) ────────────────────────────────
    # The backend chat path already runs ``sanitize_output`` (chat router /
    # ai_router) before any response leaves the API. The bot path never did —
    # so RAG/bridge context could leak internal identifiers (hostnames like
    # internal hostnames, gopass paths, repo paths, delimiter
    # tokens) straight into Telegram. Run the SAME scrub here, on the FINAL
    # answer, so the snappy/long remote path AND the tools fallback path are
    # both covered (this funnel is the single exit every provider uses).
    # Deliberately last: the guardrail can append citation/no-live-data lines
    # above, and the degenerate replacement must happen before this scrub.
    answer = sanitize_output(answer)

    return answer, live_results, stripped_syms, stripped_addrs, answer_is_degenerate


__all__ = [
    "_injection_warning",
    "_post_guardrail",
    "_reply_local_intent",
    "_reply_redirect",
    "_run_entry_guards",
    "_scam_address_warning",
]
