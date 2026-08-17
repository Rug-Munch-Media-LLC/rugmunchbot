#!/usr/bin/env python3
"""
RugMunchBot — Anti-abuse / anti-poisoning / anti-scam protections (v6).

Layers:
  1. Prompt-injection detection: blocks common "ignore your instructions",
     "system prompt", and crypto-scam social-engineering patterns before the
     text reaches the RAG/LLM pipeline or gets echoed back to other users.
  2. Known-scam address denylist: seeded from env (RUGMUNCH_SCAM_DENYLIST,
     comma-separated) and grown at runtime from user /report submissions
     (db.scam_reports). Shared addresses on the denylist are blocked with a
     warning instead of being auto-scanned / forwarded.

Every check is pure and testable: no network I/O, no side effects.
"""

from __future__ import annotations

import os
import re

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.anti_abuse")

# ══════════════════════════════════════════════
# 1. PROMPT INJECTION / POISONING
# ══════════════════════════════════════════════
# (compiled pattern, human-readable category). The message handler returns
# the category so the bot can show a targeted warning instead of a generic
# "I don't understand" reply.
_PROMPT_INJECTION_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(ignore|disregard|forget|override|skip|bypass)\s+"
            r"(all\s+)?(previous|prior|above|your)\s+(instructions?|prompts?|"
            r"rules?|system\s+prompt|constraints?|guidelines?|context)\b",
            re.IGNORECASE,
        ),
        "instruction-override",
    ),
    (
        re.compile(
            r"\b(reveal|show|print|output|leak|expose|tell\s+me)\s+"
            r"(your\s+)?(system\s+prompt|instructions?|prompt|initial\s+prompt|"
            r"secret|api\s+key|token|password|private\s+key|seed\s+phrase)\b",
            re.IGNORECASE,
        ),
        "secret-extraction",
    ),
    (
        re.compile(
            r"\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on\s+"
            r"you(?:'re| are)|jailbreak|developer\s+mode|dan\s+mode|"
            r"no\s+filters|uncensored\s+mode)\b",
            re.IGNORECASE,
        ),
        "role-switch",
    ),
    (
        re.compile(
            r"\b(repeat|echo|copy|print)\s+(this\s+)?(exact|same|above)\s+"
            r"(message|text|prompt|instruction)\b",
            re.IGNORECASE,
        ),
        "echo-poisoning",
    ),
    (
        re.compile(
            r"\b(send\s+me\s+your|give\s+me\s+your|what\s+is\s+your)\s+"
            r"(seed\s+phrase|private\s+key|mnemonic|passphrase)\b",
            re.IGNORECASE,
        ),
        "wallet-secret-harvest",
    ),
    (
        re.compile(
            r"\b(claim|verify|connect|approve|sign)\s+"
            r"(your\s+)?(wallet|airdrop|reward|bonus)\s+"
            r"(to\s+)?(receive|claim|get|verify)\b",
            re.IGNORECASE,
        ),
        "wallet-drain-social",
    ),
    (
        re.compile(
            r"\b(promote|shill|advertise|post|share)\s+this\s+(token|coin|"
            r"message|text|project)\b",
            re.IGNORECASE,
        ),
        "forced-promotion",
    ),
]

# Standalone hard blocklist — a message consisting (mostly) of one of these
# is rejected outright before any downstream processing.
_PROMPT_INJECTION_HARD_BLOCKS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^\s*(ignore|disregard|forget)\s+(all\s+)?previous\s+instructions?\s*[.!]?\s*$", re.IGNORECASE),
        "instruction-override",
    ),
    (
        re.compile(r"^\s*what\s+is\s+your\s+(system\s+)?prompt\s*[?]?\s*$", re.IGNORECASE),
        "secret-extraction",
    ),
]


def check_prompt_injection(text: str) -> str | None:
    """Return the category of the prompt-injection attempt, or None.

    ``text`` is the raw user message. Matches both the standalone hard-block
    patterns and the conversational soft patterns.
    """
    if not text or not text.strip():
        return None
    for pattern, category in _PROMPT_INJECTION_HARD_BLOCKS:
        if pattern.search(text):
            return category
    for pattern, category in _PROMPT_INJECTION_RULES:
        if pattern.search(text):
            return category
    return None


# ══════════════════════════════════════════════
# 2. KNOWN SCAM ADDRESSES
# ══════════════════════════════════════════════
# Env-seeded denylist (comma separated). Grown at runtime by /report (see
# db.report_scam / db.get_known_scam_addresses).
_ENV_DENYLIST: set[str] = {a.strip().lower() for a in os.getenv("RUGMUNCH_SCAM_DENYLIST", "").split(",") if a.strip()}


def is_known_scam_address(address: str) -> bool:
    """Return True when ``address`` is on the known-scam denylist.

    Checks the env-seeded set first (cheap, no DB), then the runtime
    /report-driven set from the bot SQLite DB. Fail-open: if the DB read
    fails, only the env set is consulted so the bot never crashes on a
    message.
    """
    if not address:
        return False
    addr = address.strip().lower()
    if addr in _ENV_DENYLIST:
        return True
    try:
        from app.domains.telegram.rugmunchbot import db

        return addr in db.get_known_scam_addresses()
    except Exception:
        logger.debug("known_scam_addresses lookup failed", exc_info=True)
        return False


def register_reported_scam(address: str) -> None:
    """Add an address to the in-memory env denylist set after a /report.

    The durable copy lives in the DB (db.report_scam); this mirrors it in
    memory so subsequent messages in the same process are blocked without a
    DB round-trip.
    """
    if not address:
        return
    _ENV_DENYLIST.add(address.strip().lower())


__all__ = [
    "check_prompt_injection",
    "is_known_scam_address",
    "register_reported_scam",
]
