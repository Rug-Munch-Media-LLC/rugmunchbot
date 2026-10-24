"""Scanner engine — snappy-tier AI verdict over the structured scan.

After the structured scan completes, the snappy LLM (free OpenRouter via
``app.llm_config.get_snappy_config``, routed through ``chat_with_fallback``)
renders a 2-4 sentence plain-English verdict + "what to watch" from the
KEY facts only. Fail-open: ANY failure returns "" so the caller keeps the
existing template verdict — never an exception, never a hang.
"""

from __future__ import annotations

import asyncio
import html
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.scanner._ai_grounding import grounded_text
from rugmunchbot.tools.scanner._ai_verdict_facts import (
    verdict_facts as _verdict_facts,
)

logger = get_logger("rmi_bot.tools.scanner")

# ── AI verdict (snappy-tier summary over the structured scan) ─────────────
# After the structured scan completes, the snappy LLM (free OpenRouter via
# ``app.llm_config.get_snappy_config`` — routed through
# ``chat_with_fallback`` so a free-tier rate-limit degrades to the cheap
# PAID model instead of dying) renders a 2-4 sentence plain-English verdict
# + "what to watch" from the KEY facts only. Fail-open: ANY failure (timeout,
# rate-limit, missing key, config error) returns "" so the caller keeps the
# existing template verdict (``format_verdict_header`` inside
# ``format_scan_report``) — never an exception, never a hang.
_AI_VERDICT_TIMEOUT = 10.0  # hard cap — the verdict must never stall /scan
_AI_VERDICT_FACTS_CHARS = 600
_AI_VERDICT_MAX_CHARS = 1200

_AI_VERDICT_SYSTEM = (
    "You are the RugMunch scan analyst. Write a SHORT, plain-English risk "
    "verdict for a token scan. Use ONLY the facts provided — never invent "
    "numbers. 2-4 sentences, trader voice, no hype, no markdown, no emojis. "
    "When holder_sample_size is present, cite it as analysis depth (e.g. "
    "'across 300 sampled wallets') and weight concentration findings by it. "
    "When bundle_cluster_score >= 70, call the launch coordinated. "
    "When flow_concentrated is true, warn that one wallet dominates recent "
    "transfers (flow_top_mover_share_pct) — classic accumulation-or-dump setup. "
    "When rug_prediction is present, lead with it: 'Rug prediction: N/100 "
    "(BAND)' then explain the top drivers. "
    "When deployer_tokens_launched > 1 and deployer_rugged_pct >= 40, call "
    "out the repeat-rug deployer explicitly ('this deployer shipped N tokens, "
    "X% rugged'). "
    "End with a one-line 'What to watch:' pointing at the riskiest signal or, "
    "when the scan is clean, at liquidity/holder concentration."
)


async def _llm_verdict_text(result: dict[str, Any]) -> str:
    """One bounded snappy-tier call. Returns the verdict text or "" (never raises)."""
    import json

    from rugmunchbot.llm_config import chat_with_fallback

    facts = _verdict_facts(result)
    prompt = (
        "Structured token scan facts (JSON):\n"
        f"{json.dumps(facts, default=str)[:_AI_VERDICT_FACTS_CHARS]}\n\n"
        "Write the verdict now."
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _AI_VERDICT_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    text = await asyncio.wait_for(
        chat_with_fallback(messages, "snappy"), timeout=_AI_VERDICT_TIMEOUT
    )
    raw = (text or "").strip()
    grounded = grounded_text(raw, facts)
    if grounded != raw:
        logger.debug("ai_verdict_ungrounded_sentences_dropped")
    return grounded


async def render_ai_verdict(result: dict[str, Any]) -> str:
    """Render an HTML-safe ``🤖 AI Verdict`` block over ``result``.

    Passes the key scan facts (risk score, honeypot/rug flags, LP lock,
    mint/freeze auth, taxes, holders) to the snappy LLM (via
    ``app.llm_config.get_snappy_config()``) for a 2-4 sentence natural-language
    verdict + "what to watch". Fail-open: any LLM failure returns "" so the
    caller falls back to the existing template verdict that
    ``format_scan_report`` already renders. The returned block is
    HTML-escaped (LLM output is untrusted) and safe for ``ParseMode.HTML``.
    """
    try:
        text = await _llm_verdict_text(result)
    except Exception:
        logger.debug("ai_verdict_failed", exc_info=True)
        text = ""
    text = (text or "").strip()
    if not text:
        return ""
    return f"\U0001f916 <b>AI Verdict</b>\n{html.escape(text)[:_AI_VERDICT_MAX_CHARS]}"
