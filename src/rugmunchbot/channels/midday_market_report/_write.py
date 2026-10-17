"""Midday Market Report — the LLM writer, including the SKIP sentinel.

Split out of ``_publish.py`` so that file stays on the shrink-only side of the
200-line cap. A literal "SKIP" is the model's escape hatch when the prompt
demands links it was not given; it must be retried with an explicit
"write from the numbers" directive, never logged as a short write.
"""

from __future__ import annotations

import logging
from typing import Any

from rugmunchbot.channels.midday_market_report._pipeline import is_skip, strip_echo
from rugmunchbot.channels.midday_market_report._prompt import build_messages

logger = logging.getLogger("rmi_bot.channels.midday_market_report")

TASK_CLASS = "market_brief"

NO_SKIP_DIRECTIVE = (
    "\n\nDO NOT return SKIP. Write the full 3000-4000 character macro report from "
    "the numbers above; if no links are available, write without links."
)


async def _generate(messages: list[dict[str, str]], context: dict[str, Any]) -> str:
    """One LLM call, stripped of prompt echo. Returns "" on failure."""
    from rugmunchbot.llm_config import chat_with_fallback

    try:
        raw = await chat_with_fallback(messages, "long", task_class=TASK_CLASS)
    except Exception as exc:
        logger.warning("midday_report:llm_failed err=%s", str(exc)[:160])
        return ""
    return strip_echo(raw)


async def write_report(context: dict[str, Any]) -> str:
    """LLM-write the report: SKIP retry, then one length-retry."""
    raw = await _generate(build_messages(context), context)
    if is_skip(raw):
        logger.warning("midday_report:llm_skip_retrying")
        retry = build_messages(context)
        retry[1]["content"] += NO_SKIP_DIRECTIVE
        raw = await _generate(retry, context)
        if is_skip(raw):
            logger.warning("midday_report:llm_skip sources=%s", context.get("sources"))
            return ""

    if len(raw.strip()) < 1200:
        logger.warning("midday_report:llm_short retrying chars=%s", len(raw.strip()))
        retry = build_messages(context)
        retry[1]["content"] += NO_SKIP_DIRECTIVE + (
            "\n\nIMPORTANT: your previous attempt was too short. Write the FULL "
            "3000-4000 character report now — every section, every item."
        )
        longer = await _generate(retry, context)
        if len(longer.strip()) > len(raw.strip()):
            raw = longer
    return raw


__all__ = ["NO_SKIP_DIRECTIVE", "TASK_CLASS", "write_report"]
