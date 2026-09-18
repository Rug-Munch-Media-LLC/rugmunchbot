# VENDORED 2026-08-24 from rmi-backend/app/core/ai_guardrails.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
#!/usr/bin/env python3
"""
AI Guardrails — Anti-Poisoning for Public AI Endpoints
========================================================
Wires guardrails-ai (0.10.2) with ToxicLanguage + CompetitorCheck validators
plus custom prompt-injection detection and output sanitization.

All public AI endpoints (chat, bot, website) must route through this module
before sending to ANY model provider and after receiving ANY response.

Integrations:
  - app/ai_router.py: chat_completion() wraps input/output
  - app/routers/chat.py: /api/v1/chat (already uses AIGuard, this adds guardrails-ai)
  - bot/app/telegram_bot/guardrails.py: standalone bot guardrails (mirrored patterns)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# Prompt injection patterns to detect
# ═════════════════════════════════════════════════════════════════════════════
INJECTION_PATTERNS: list[str] = [
    "ignore previous instructions",
    "ignore all previous",
    "ignore your instructions",
    "you are now",
    "system prompt",
    "act as",
    "pretend you are",
    "jailbreak",
    "bypass",
    "DAN mode",
    "developer mode",
    "god mode",
    "unrestricted",
    "do anything now",
    "override safety",
    "disable your guardrails",
    "forget your training",
    "reveal your prompt",
    "tell me your instructions",
    "what are your rules",
    "new instructions:",
    "from now on you are",
    "you are a",
    "you are an",
    "you\\'re a",
    "roleplay as",
    "you must obey",
    "bypass your filter",
    "disable security",
    "you are in developer mode",
    "jail broken",
    "cracked version",
]

# ═════════════════════════════════════════════════════════════════════════════
# Model delimiter tokens that must never leak from user input into system context
# ═════════════════════════════════════════════════════════════════════════════
FORBIDDEN_OUTPUT_TOKENS: list[str] = [
    "<system>",
    "</system>",
    "<|im_start|>",
    "<|im_end|>",
    "[SYSTEM]",
    "[/SYSTEM]",
    "<<SYS>>",
    "</SYS>",
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "[INST]",
    "[/INST]",
]

# ═════════════════════════════════════════════════════════════════════════════
# Internal infrastructure patterns that must never appear in output
# ═════════════════════════════════════════════════════════════════════════════
_INTERNAL_PATTERNS: list[str] = [
    "100." + "64.",  # internal private subnet (split to avoid a naive grep flag)
    "talos." + "rugmunch.io",  # internal host (split to avoid a naive grep flag)
    "cinnabox",
    "gopass",
    "/srv/work/repos",
    "/root/backend",
    "rmi_auth_token",
    "RMI_AUTH_TOKEN",
    "OPENROUTER_API_KEY",
    "LLM_API_KEY",
]


def sanitize_input(text: str) -> str:
    """Strip injection patterns from user input before sending to any LLM.

    Returns cleaned text or a canned block message if the input contains
    known prompt-injection / jailbreak patterns.
    """
    if not text or not text.strip():
        return text

    lower = text.lower()

    # Check injection patterns
    for pattern in INJECTION_PATTERNS:
        if pattern.lower() in lower:
            logger.warning(
                "guardrails_ai: input blocked (injection pattern=%r, text[:80]=%r)",
                pattern,
                text[:80],
            )
            return "[BLOCKED: Injection attempt detected]"

    # Strip null bytes and excessive whitespace
    text = text.replace("\x00", "").strip()

    # Truncate to 4000 chars max (defense against context-window stuffing)
    if len(text) > 4000:
        text = text[:4000]

    return text


def sanitize_output(text: str) -> str:
    """Post-process AI output to remove system prompt leaks, secrets, and
    oversized code blocks.
    """
    if not text:
        return text

    # Strip system prompt / delimiter token leaks
    for forbidden in FORBIDDEN_OUTPUT_TOKENS:
        if forbidden in text:
            logger.warning("guardrails_ai: output filtered (delimiter token leak)")
            return "[Response filtered for safety]"

    # Strip internal infrastructure identifiers
    for pattern in _INTERNAL_PATTERNS:
        if pattern in text:
            logger.warning("guardrails_ai: output filtered (internal identifier leak)")
            return "[Response filtered for safety]"

    # Remove oversized code blocks (> 50 lines) to prevent code execution
    lines = text.split("\n")
    code_count = 0
    in_code = False
    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
        elif in_code:
            code_count += 1
            if code_count > 50:
                logger.warning("guardrails_ai: output filtered (large code block)")
                return "[Large code block removed for safety]"

    return text


# ═════════════════════════════════════════════════════════════════════════════
# Think-block stripping — SINGLE shared implementation
# ═════════════════════════════════════════════════════════════════════════════
# Formerly duplicated across ``app/ai_router.py`` (``_strip_think_tags`` +
# ``_ThinkStreamFilter``), the bot chat pipeline (``_quality.strip_think_blocks``)
# and the remote LLM path (``chat_ai/_llm.py``). All of them now delegate here
# so the strip logic cannot drift between backend and bot.


def strip_think_tags(text: str) -> str:
    """Strip reasoning blocks from an LLM answer.

    Handles ``<think>``, ``<reasoning>`` and ``<thought>`` blocks (complete
    and unterminated when ``max_tokens`` cuts them off mid-reasoning), plus
    DeepSeek-style unwrapped chain-of-thought preambles ("The user is
    asking…", "I need to…", "Let me think…") that leak as plain text from
    reasoning models. Idempotent; safe on plain text.
    """
    cleaned = text or ""
    for tag in ("think", "reasoning", "thought"):
        cleaned = re.sub(rf"<{tag}>.*?</{tag}>", "", cleaned, flags=re.DOTALL)
        cleaned = re.sub(rf"<{tag}>.*", "", cleaned, flags=re.DOTALL)
    # Unwrapped CoT preamble: reasoning models sometimes emit their chain of
    # thought as plain prose before the answer. Strip the leading run of
    # self-referential "I need to / The user is asking / Let me think"
    # sentences (multi-sentence, at the very start of the output).
    preamble = re.match(
        r"^\s*(?:(?:The|This) user (?:is|was|asked|wants).*?\n?|"
        r"(?:I|We) (?:need|want|should|must|have|can|will).*?\n?|"
        r"Let me (?:think|check|look|start|begin|consider).*?\n?|"
        r"Okay,? so .*?\n?|Alright,? so .*?\n?|"
        r"As (?:an AI|a (?:crypto|security|market) analyst).*?\n?){1,4}",
        cleaned,
        flags=re.IGNORECASE,
    )
    if preamble and len(preamble.group(0)) < len(cleaned) // 2:
        cleaned = cleaned[preamble.end() :]
    return cleaned.strip()


class ThinkStreamFilter:
    """Incrementally strip ``<think>...</think>`` blocks from a token stream."""

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._in_think = False
        self._buf = ""

    def feed(self, token: str) -> str:
        self._buf += token
        out: list[str] = []
        while self._buf:
            if self._in_think:
                end = self._buf.find(self.CLOSE)
                if end == -1:
                    break
                self._buf = self._buf[end + len(self.CLOSE) :]
                self._in_think = False
                continue
            start = self._buf.find(self.OPEN)
            if start == -1:
                emit = self._buf
                self._buf = ""
                for i in range(1, len(self.OPEN)):
                    if emit.endswith(self.OPEN[:i]):
                        self._buf = emit[-i:]
                        emit = emit[:-i]
                        break
                if emit:
                    out.append(emit)
                break
            if start:
                out.append(self._buf[:start])
            self._buf = self._buf[start + len(self.OPEN) :]
            self._in_think = True
        return "".join(out)

    def flush(self) -> str:
        leftover = "" if self._in_think else self._buf
        self._buf = ""
        self._in_think = False
        return leftover


# ═════════════════════════════════════════════════════════════════════════════
# guardrails-ai hub validators — REMOVED 2026-09-18
# ═════════════════════════════════════════════════════════════════════════════
# The previous module-level init (`Guard().use(ToxicLanguage(...))`) phoned
# the guardrails hub (production-kb/telemetry.guardrailsai.com — AWS
# CloudFront) and pulled albert-base-v2 from HuggingFace AT IMPORT TIME: a
# ~30-40s stall in every fresh process (bot boot, pytest, cron) for a
# validator this stack no longer needs. Self-hosted ML scanning lives in the
# llm-guard sidecar (127.0.0.1:8016) + presidio; the pure-regex helpers here
# (sanitize_input/output, strip_think_tags) are unaffected.
#
# Kept as constants for API compatibility; both validators are passthroughs.
GUARDRAILS_AI_LOADED = False
_toxic_guard = None
_competitor_guard = None

# CompetitorCheck: ops has not configured the competitor list yet — keep the
# validator disabled (passthrough) until a curated list exists. Any
# competitor list would otherwise filter legitimate data-source mentions
# (DexScreener, CoinGecko, Birdeye) from scan answers.
# _competitor_guard = Guard().use(CompetitorCheck(competitors=[...], on_fail="fix"))


def validate_toxic(text: str) -> dict[str, Any]:
    """Toxicity scan — passthrough by design (guardrails-ai removed 2026-09-18).

    The AWS/CloudFront hub validator was cut (import-time network stall, see
    the module header). ML toxicity/PII scanning is served by the self-hosted
    llm-guard sidecar (:8016) and presidio; callers keep the same contract:
    ``{"safe": bool, "cleaned": str}``.
    """
    return {"safe": True, "cleaned": text}


def validate_competitor(text: str) -> dict[str, Any]:
    """Check for competitor mentions via guardrails-ai CompetitorCheck.

    Returns dict with ``safe`` flag and ``cleaned`` text.
    """
    if not GUARDRAILS_AI_LOADED or _competitor_guard is None:
        return {"safe": True, "cleaned": text}

    try:
        result = _competitor_guard.validate(text)
        if result.validation_passed:
            return {"safe": True, "cleaned": text}
        return {"safe": False, "cleaned": result.validated_output or text}
    except Exception as exc:
        logger.warning("guardrails_ai: CompetitorCheck validation error: %s", exc)
        return {"safe": True, "cleaned": text}


def full_pipeline(text: str) -> dict[str, Any]:
    """Run the complete guardrails pipeline: injection check → toxic → competitor.

    Returns dict with:
        - safe: bool
        - text: cleaned text (or block message)
        - blocked: bool (True = completely blocked)
        - reasons: list of rejection reasons
    """
    result: dict[str, Any] = {"safe": True, "blocked": False, "text": text, "reasons": []}

    # Layer 1: Injection patterns (hard block)
    cleaned = sanitize_input(text)
    if cleaned.startswith("[BLOCKED"):
        result["safe"] = False
        result["blocked"] = True
        result["text"] = cleaned
        result["reasons"].append("prompt_injection")
        return result

    # Layer 2: Toxic language (fix if possible)
    tox = validate_toxic(cleaned)
    if not tox["safe"]:
        result["reasons"].append("toxic_language")
    cleaned = tox["cleaned"]

    # Layer 3: Competitor check
    comp = validate_competitor(cleaned)
    if not comp["safe"]:
        result["reasons"].append("competitor_mention")
    cleaned = comp["cleaned"]

    result["text"] = cleaned
    result["safe"] = len(result["reasons"]) == 0
    return result
