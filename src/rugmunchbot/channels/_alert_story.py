"""Alert → main-channel STORY writer (2026-08-26).

When an alert escalates to @cryptorugmuncher, main doesn't get the raw
alert card — it gets THE STORY written like the RugMunch founder would
post on their personal socials: what happened, why it matters, the take.
Fail-open and time-bounded: any error returns None and the caller falls
back to the standard escalation card.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.alert_story")

_STORY_TIMEOUT = 25.0

_SYSTEM_PROMPT = (
    "You are the founder of RugMunch (crypto scam intelligence, @cryptorugmuncher "
    "main channel). Write channel posts like you'd write on your personal X account, "
    "not a corporate blog:\n"
    "- Open with a hook line that names what happened.\n"
    "- Tell the STORY: what occurred, the numbers, who did what — plain degen "
    "language, zero corporate hedging.\n"
    "- Then YOUR TAKE: one short paragraph of honest commentary — pattern "
    "recognition, what it signals, snark where earned.\n"
    "- If the deployer is a known repeat offender, say so. If the pattern is "
    "obvious, name it. 'Same wallet that rugged $TOKEN last week' is better "
    "than 'a wallet associated with prior incidents.'\n"
    "- Close with a clear action: 'Don't ape.' or 'Watch the LP.' or "
    "'This one's already dead.' or 'Get out if you're in.' — not a vague "
    "'proceed with caution.'\n"
    "- Close with 'Full scan:' pointing to the scanner link provided.\n"
    "- 400-700 characters. Telegram HTML allowed: <b>, <i>, <a href>.\n"
    "- NEVER invent facts not in the evidence. If evidence is thin, say so.\n"
    "- No 'BREAKING' clickbait, no emoji spam (max 2), no hashtags.\n"
    "- Write like you're posting on your personal X account, not a corporate blog.\n"
    "- End with a clear action: 'Don't ape.' or 'Watch the LP.' or 'This one's "
    "already dead.'\n"
    "- NO AI SLOP, ever: no 'in the ever-evolving world of crypto', no 'the crypto "
    "landscape', no 'it's worth noting', no 'delve into', no 'at the end of the day', "
    "no generic hype, no hedging. Name the deployer, the size, the mechanism — write "
    "it like a human who is genuinely annoyed, not like a language model filling "
    "space. If it's a classic rug, say 'another classic rug' and mean it.\n"
    "- NEVER use: NFA, DYOR, GM, wagmi, stay safe, frens, to the moon. "
    "These are not our voice. We warn, we don't disclaim.\n"
    "- Vary your sentence length. Short punches mixed with longer analysis. "
    "Not every sentence should be the same rhythm.\n"
)


def _evidence(item: dict[str, Any]) -> str:
    bits = [
        f"event={item.get('condition') or item.get('alert_type') or 'signal'}",
        f"token={item.get('name') or item.get('symbol') or '?'}",
        f"chain={item.get('chain') or '?'}",
        f"severity={item.get('risk_level') or '?'}",
    ]
    for k in ("summary", "verdict", "title"):
        v = str(item.get(k) or "").strip()
        if v:
            bits.append(f"{k}={v[:300]}")
    addr = str(item.get("address") or item.get("token_address") or "").strip()
    if addr:
        bits.append(f"address={addr}")
    return "; ".join(bits)


def _scanner_url(item: dict[str, Any]) -> str:
    url = str(item.get("scanner_url") or "").strip()
    return url


async def write_alert_story(item: dict[str, Any]) -> str | None:
    """Story-style main-channel post for an escalated alert (None on failure)."""
    try:
        from rugmunchbot.llm_config import chat_with_fallback
    except Exception as exc:
        logger.warning("alert_story:llm_import_failed error=%s", str(exc)[:100])
        return None

    scanner = _scanner_url(item)
    prompt = (
        f"Write today's main-channel post about this alert.\n\n"
        f"EVIDENCE (only source of truth): {_evidence(item)}\n\n"
        + (f"Scanner link to include verbatim as 'Full scan: {scanner}'\n" if scanner else "")
        + "\nWrite the post now."
    )
    try:
        async with asyncio.timeout(_STORY_TIMEOUT):
            out = await chat_with_fallback(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "standard",
            )
    except Exception as exc:  # fail-open by contract
        logger.debug("alert_story_failed err=%s", str(exc)[:120])
        return None
    out = (out or "").strip()
    return out if len(out) > 120 else None
