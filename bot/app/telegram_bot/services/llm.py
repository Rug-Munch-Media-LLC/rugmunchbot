#!/usr/bin/env python3
"""
V3 LLM client — Qwen3-4B chat + DeepSeek-8B analysis via llama-server (OpenAI-compatible).

Used by /analyze (token verdict grounded in scanner context) and /market
(market rundown). Output is sanitized (defense in depth) to redact internal
IPs / secret-like tokens before it reaches the user.
"""

import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from config import LLM_CHAT_URL, LLM_MODEL, MARKET_RUNDOWN_URL

logger = logging.getLogger(__name__)

LLM_TIMEOUT = 45.0
MARKET_TIMEOUT = 30.0

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_SECRET_RE = re.compile(
    r"\b(?:sk|pk|private|secret|api)[-_]?key\b|"
    r"\b(?:[0-9a-fA-F]{64}|[1-9A-HJ-NP-Za-km-z]{88})\b",
    re.IGNORECASE,
)


def sanitize_output(text: str) -> str:
    """Redact obvious secrets and internal IPs from model output."""
    safe = text or ""
    safe = _SECRET_RE.sub("[REDACTED]", safe)
    safe = _IP_RE.sub("[IP]", safe)
    return safe


def build_analyze_system_prompt(address: str, scan_ctx: dict) -> str:
    """System prompt for /analyze — hardened directives + scanner context."""
    risk = scan_ctx.get("risk_score") or 0
    tier = scan_ctx.get("risk_tier") or "unknown"
    warnings = scan_ctx.get("warnings") or scan_ctx.get("factors") or []
    liq = scan_ctx.get("liquidity") or {}
    deployer = scan_ctx.get("deployer") or {}

    ctx_lines = [
        f"Token: {address}",
        f"Risk score: {risk}/100 (tier: {tier})",
    ]
    if warnings:
        ctx_lines.append("Warnings: " + "; ".join(str(w) for w in warnings[:5]))
    if liq.get("usd"):
        ctx_lines.append(f"Liquidity: ${liq['usd']:,.0f}")
    if liq.get("locked_pct") is not None:
        ctx_lines.append(f"LP locked: {float(liq['locked_pct']) * 100:.0f}%")
    if deployer.get("other_tokens"):
        ctx_lines.append(
            f"Deployer has {deployer['other_tokens']} other tokens "
            f"(reputation: {deployer.get('reputation', 'unknown')})"
        )

    return (
        "You are RugMunch AI, a crypto security analyst embedded in the @rugmunchbot "
        "Telegram bot. Answer in short, plain language (max 250 words). Be direct about "
        "risk. Never give financial advice or price predictions; always note that DYOR "
        "applies. Use the scanner context below as ground truth; if the context is empty, "
        "say what data is missing rather than inventing facts.\n\n"
        "CONTEXT (scanner intelligence):\n"
        + ("\n".join(ctx_lines) if ctx_lines else "No scanner context available.")
        + "\n\nUSER REQUEST: Analyse this token's safety and return a verdict."
    )


def _extract_text(body) -> str:
    """Extract the answer text from flexible JSON responses (OpenAI-style or plain)."""
    if isinstance(body, str):
        return body
    if isinstance(body, dict):
        for key in ("rundown", "text", "content", "response", "answer", "result"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message", {})
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str) and content:
                        return content
    return ""


async def analyze_token(address: str, scan_ctx: dict) -> str:
    """Run DeepSeek-8B over scanner context and return a sanitized verdict."""
    system = build_analyze_system_prompt(address, scan_ctx)
    user = f"Token contract: {address}\nIs this token safe to interact with?"
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 600,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        r = await client.post(LLM_CHAT_URL, json=payload)
        r.raise_for_status()
        raw = _extract_text(r.json())
    if not raw:
        raise ValueError("LLM returned empty completion")
    return sanitize_output(raw)


async def fetch_market_rundown() -> str:
    """Fetch the market rundown from the llama-server chat endpoint."""
    async with httpx.AsyncClient(timeout=MARKET_TIMEOUT) as client:
        r = await client.get(MARKET_RUNDOWN_URL)
        r.raise_for_status()
        try:
            body = r.json()
        except ValueError:
            body = r.text
        text = _extract_text(body)
    if not text:
        raise ValueError("market rundown endpoint returned empty payload")
    return sanitize_output(text)
