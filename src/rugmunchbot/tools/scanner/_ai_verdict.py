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

from app.domains.telegram.rugmunchbot.support.logging import get_logger

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
    "End with a one-line 'What to watch:' pointing at the riskiest signal or, "
    "when the scan is clean, at liquidity/holder concentration."
)


def _verdict_facts(result: dict[str, Any]) -> dict[str, Any]:
    """Project the structured scan result onto the small fact set the LLM
    verdict is allowed to cite. Every value is optional (None when absent) so
    the prompt never contains fabricated data. Reads both shapes: the legacy
    top-level fields (``honeypot``/``mintable``/``buy_tax``…) and the v2
    ``evidence`` security checks (HP01 honeypot, CR03 mintable, LP, holders).
    """
    risk = result.get("risk_score")
    try:
        risk_f = round(float(risk), 1) if risk is not None else None
    except (TypeError, ValueError):
        risk_f = None

    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    ev_sec = evidence.get("security")
    if not isinstance(ev_sec, dict):
        ev_sec = {}
    ev_checks = ev_sec.get("checks")
    if not isinstance(ev_checks, dict):
        ev_checks = {}
    ev_holders = evidence.get("holders")
    if not isinstance(ev_holders, dict):
        ev_holders = {}
    ev_liq = evidence.get("liquidity")
    if not isinstance(ev_liq, dict):
        ev_liq = {}

    checks = result.get("security_checks")
    if not isinstance(checks, dict):
        checks = {}
    for code, detail in ev_checks.items():
        if isinstance(detail, dict) and code not in checks:
            checks[str(code)] = detail

    def status(code: str) -> str | None:
        c = checks.get(code)
        return c.get("status") if isinstance(c, dict) else None

    flags = [str(f) for f in (result.get("risk_flags") or result.get("threats") or []) if f]
    total_holders = result.get("holders")
    if not total_holders:
        total_holders = ev_holders.get("holder_count")
    top10 = (result.get("holder_analysis") or {}).get("top_10_pct") or ev_holders.get("top_10_pct")
    lp_locked = result.get("lp_locked")
    if lp_locked is None and ev_liq.get("locked_pct") is not None:
        try:
            lp_locked = float(ev_liq["locked_pct"]) / 100
        except (TypeError, ValueError):
            lp_locked = None

    return {
        "name": result.get("name"),
        "symbol": result.get("symbol"),
        "chain": result.get("chain"),
        "risk_score": risk_f,
        "risk_level": result.get("risk_level"),
        "honeypot": result.get("honeypot") or status("HP01") or status("HP04"),
        "mintable": result.get("mintable") or status("CR03"),
        "can_blacklist": result.get("can_blacklist"),
        "owner_renounced": result.get("owner_renounced"),
        "contract_verified": result.get("contract_verified"),
        "buy_tax": result.get("buy_tax"),
        "sell_tax": result.get("sell_tax"),
        "lp_locked": lp_locked,
        "total_holders": total_holders,
        "top_10_holder_pct": top10,
        "bundle_detected": result.get("bundle_detected"),
        "fresh_wallet_pct": result.get("fresh_wallet_pct"),
        "exchange_funded_pct": result.get("exchange_funded_pct"),
        "flagged_security_checks": {
            str(k): (v.get("status") if isinstance(v, dict) else None)
            for k, v in checks.items()
            if isinstance(v, dict) and v.get("status") in ("fail", "pass")
        },
        "threats": flags[:6],
    }


async def _llm_verdict_text(result: dict[str, Any]) -> str:
    """One bounded snappy-tier call. Returns the verdict text or "" (never raises)."""
    import json

    from app.llm_config import chat_with_fallback

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
    text = await asyncio.wait_for(chat_with_fallback(messages, "snappy"), timeout=_AI_VERDICT_TIMEOUT)
    return (text or "").strip()


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
