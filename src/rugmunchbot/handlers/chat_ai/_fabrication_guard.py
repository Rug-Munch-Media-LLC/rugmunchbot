"""Zero-fabrication guardrail — the post-answer data-integrity pass.

Split out of ``_guards.py``: that module owns the Telegram funnel, this one owns
the two answer-rewriting passes — symbol/address corroboration and numeric
verification — plus the honest no-live-data note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot.handlers.chat_ai._numeric_guard import (
    _collect_numeric_facts,
    _verify_numeric_claims,
)
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools import known_addresses, known_symbols
from rugmunchbot.tools.guardrails import (
    NO_LIVE_DATA_MESSAGE,
    fabrication_note,
    sanitize_answer,
)

if TYPE_CHECKING:
    from rugmunchbot.tools.registry import ToolResult

__all__ = ["apply_fabrication_guardrail"]

logger = get_logger("rmi_bot.chat_ai")


def _tools_corroborate(live_results: dict[str, ToolResult], was_fallback: bool) -> bool:
    """True when our own tools returned real data worth verifying against.

    web_search's synthesized fallbacks (knowledge answer / honest no-live-data)
    are NOT corroborating live data — they carry no tool-verified symbols, so
    counting them would flip tools_ok and make the guardrail strip every ticker
    from a backend-grounded answer (empty known set).
    """
    tools_ok = any(
        r.ok and not (r.tool == "web_search" and str(r.source).startswith("knowledge"))
        for r in live_results.values()
    )
    return was_fallback or tools_ok


def _apply_numeric_verification(
    answer: str, user_id: int, live_results: dict[str, ToolResult]
) -> str:
    """Flag every $amount / ±% claim that no fetched datum corroborates."""
    known_nums = _collect_numeric_facts(live_results)
    if not known_nums:
        return answer
    answer, flagged_nums = _verify_numeric_claims(answer, known_nums)
    if flagged_nums:
        logger.info(
            "guardrail flagged unverified numbers user=%s nums=%s", user_id, flagged_nums[:5]
        )
    return answer


def _strip_fabrications(
    answer: str, user_id: int, live_results: dict[str, ToolResult]
) -> tuple[str, list[str], list[str]]:
    """Strip symbols/addresses the bot never received from a tool call."""
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
    return answer, stripped_syms, stripped_addrs


def apply_fabrication_guardrail(
    answer: str,
    user_id: int,
    live_results: dict[str, ToolResult],
    was_fallback: bool,
) -> tuple[str, list[str], list[str]]:
    """Symbol/address corroboration → numeric pass → honest no-live-data note.

    The strict pass (strip anything a tool did not return) only runs when our
    own tools supplied data: on the backend path the backend's grounding is
    authoritative. Returns ``(answer, stripped_syms, stripped_addrs)``.
    """
    stripped_syms: list[str] = []
    stripped_addrs: list[str] = []
    if not live_results:
        return answer, stripped_syms, stripped_addrs
    if _tools_corroborate(live_results, was_fallback):
        answer, stripped_syms, stripped_addrs = _strip_fabrications(answer, user_id, live_results)
        answer = _apply_numeric_verification(answer, user_id, live_results)
    if was_fallback and not any(r.ok for r in live_results.values()):
        answer = f"{answer}\n\n{NO_LIVE_DATA_MESSAGE}"
    return answer, stripped_syms, stripped_addrs
