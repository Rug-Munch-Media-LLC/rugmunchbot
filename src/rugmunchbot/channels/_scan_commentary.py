"""Bounded one-line AI wit for @rmiscans scanner cards (fail-open).

Attaches a short, sharp, sometimes funny — but always HONEST — one-liner to
a scan note before it is formatted into a card. The line is grounded ONLY in
the note's real flags/risk: never fabricates a scam that is not flagged.
Every call is time-bounded (``_COMMENTARY_TIMEOUT``) and any failure returns
the item untouched, so the tick is never blocked and a card always ships.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.scan_commentary")

#: Hard budget per one-liner (snappy tier is cheap, but a hung provider must
#: never stall the channel tick).
_COMMENTARY_TIMEOUT = 4.0
#: Defensive re-clip on the model output (prompt asks for ≤180 chars).
_COMMENTARY_MAX_CHARS = 200

_SYSTEM_PROMPT = (
    "You write ONE witty one-liner for a crypto scam-scanner Telegram channel. "
    "Given a token's real scan flags, write a short (max 180 chars), sharp, "
    "sometimes funny but always HONEST line about why the token is risky. "
    "Base it ONLY on the flags given — never invent a scam, flag, or detail "
    "that is not listed. No markdown, no emojis, no quotes, no leading labels."
)


def _label(item: dict[str, Any]) -> str:
    """Best-effort token label for the prompt (never "Unknown (???)")."""
    name = str(item.get("name") or "").strip()
    symbol = str(item.get("symbol") or "").strip()
    if not name or name.lower() in ("unknown", "???", "n/a", "null", "none"):
        name = symbol
    if symbol and symbol.lower() != name.lower():
        return f"{name} ({symbol})"
    return name or "this token"


async def attach_scan_commentary(item: dict[str, Any]) -> dict[str, Any]:
    """Attach a bounded snappy-tier one-liner to *item* under ``commentary``.

    Calls ``app.llm_config.chat_with_fallback(messages, "snappy")`` under
    ``asyncio.wait_for`` (``_COMMENTARY_TIMEOUT``). Fail-open by contract:
    any error, timeout, or empty answer leaves the item untouched (no
    ``commentary`` key) so the card still ships. Tokens with nothing notable
    (no flags, not medium/high/critical) are skipped entirely — no LLM call
    for a clean scan.
    """
    flags = [str(f) for f in (item.get("risk_flags") or []) if str(f).strip()]
    risk = str(item.get("risk_level") or "unknown").lower()
    if not flags and risk not in ("critical", "high", "medium"):
        return item
    chain = str(item.get("chain") or "").strip()
    score = item.get("risk_score")
    score_text = ""
    if isinstance(score, (int, float)):
        with suppress(TypeError, ValueError):
            score_text = f"risk {float(score):.0f}/100"
    flags_text = ", ".join(flags[:6]) if flags else risk
    prompt = (
        f"Token: {_label(item)}"
        f"{f' on {chain}' if chain else ''}. "
        f"{score_text} ({risk}). Flags: {flags_text}.\\n\\nWrite the one-liner now."
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    try:
        from app.llm_config import chat_with_fallback

        text = await asyncio.wait_for(chat_with_fallback(messages, "snappy"), timeout=_COMMENTARY_TIMEOUT)
    except Exception:
        logger.debug("channels:scan_commentary_failed token=%.40s", _label(item), exc_info=True)
        return item
    # Collapse escaped AND literal newlines so the card keeps its single-line
    # one-liner layout (models often emit either form despite "no markdown").
    text = (text or "").strip().replace("\\n", " ").replace("\n", " ")
    if not text:
        return item
    text = text[:_COMMENTARY_MAX_CHARS].strip()
    if text:
        item["commentary"] = text
    return item
