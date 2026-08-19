"""RugMunchBot LLM config + fallback chain — BOT-LOCAL.

Phase 2a extraction of the bits the bot uses from rmi-backend's
``app.llm_config`` (frozen copy of the same logic; env-driven, reads the
bot's own environment — ``OPENROUTER_BACKUP_API_KEY`` and
``KILOCODE_API_KEY`` come from ``/srv/secrets/rugmunchbot.env``).

Chain (canonical — updated 2026-08-19):
  snappy = ``stepfun/step-3.7-flash:free`` via KiloCode (fast chat, free)
  long   = ``nvidia/nemotron-3.5-lightning:free`` via KiloCode (deep analysis, fast)
  paid   = ``openai/gpt-oss-20b:free`` via OpenRouter (last-resort fallback)
On ANY free-tier failure (429 / non-200 / timeout / empty) the call is
retried once with the paid ``:lowest`` model so chat never dies on a
free-tier rate-limit. Returns ``""`` when both fail — callers fail open.
Never raises; never logs the API key.

Note: StepFlash on OpenRouter is 402 (needs credits). StepFlash IS free on
KiloCode. Lightning is snappier than Ultra for detailed analysis.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

OPENROUTER_BASE = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_BACKUP_API_KEY = os.getenv("OPENROUTER_BACKUP_API_KEY", "")
KILOCODE_BASE = os.getenv("KILOCODE_BASE", "http://127.0.0.1:18091/api/gateway")
KILOCODE_API_KEY = os.getenv("KILOCODE_API_KEY", "")


def get_snappy_config() -> dict[str, Any]:
    """Config for fast chat answers (StepFlash 3.7 via KiloCode, FREE).

    StepFlash delivers quick, snappy responses with 256K context.
    Falls back to OpenRouter GPT-OSS if KiloCode is down.
    max_tokens 2000 — the snappy prompt tells the model to write the
    COMPLETE answer when a question needs depth; the old cap truncated
    detailed answers mid-sentence. Multi-part delivery splits long answers.
    """
    return {
        "model": "stepfun/step-3.7-flash:free",
        "base_url": f"{KILOCODE_BASE}/chat/completions",
        "api_key": KILOCODE_API_KEY,
        "fallback_model": "openai/gpt-oss-20b:free",
        "fallback_base_url": f"{OPENROUTER_BASE}/chat/completions",
        "fallback_api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 2000,
        "temperature": 0.3,
        "timeout": 20.0,
    }


def get_long_config() -> dict[str, Any]:
    """Config for deep analysis answers (Nemotron 3.5 Lightning via KiloCode, FREE).

    Lightning is snappier than Nemotron Ultra — delivers deep analysis in
    ~1/5 the time. 256K context window fits full contract audits + reports.
    Falls back to OpenRouter Nemotron Ultra:free if KiloCode is down.
    max_tokens 3000 — deep analysis needs the budget to finish the
    full structure (direct answer → figures → evidence → caveat → close).
    120s timeout — Lightning can take 30-60s for a full analysis.
    """
    return {
        "model": "nvidia/nemotron-3.5-lightning:free",
        "base_url": f"{KILOCODE_BASE}/chat/completions",
        "api_key": KILOCODE_API_KEY,
        "fallback_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "fallback_base_url": f"{OPENROUTER_BASE}/chat/completions",
        "fallback_api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 3000,
        "temperature": 0.7,
        "timeout": 120.0,
    }


def get_cheap_fallback_config() -> dict[str, Any]:
    """Free last-resort fallback when primary tiers fail.

    Uses GPT-OSS 20B via OpenRouter free tier. Zero cost.
    30s timeout — should be fast enough for a lightweight model.
    """
    return {
        "model": "openai/gpt-oss-20b:free",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 2000,
        "temperature": 0.5,
        "timeout": 30.0,
    }


async def _chat_once(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
) -> str:
    """One OpenAI-compatible chat POST to ``config``. Returns stripped content
    or ``""``. Uses tier-specific timeout from config. Never raises."""
    api_key = str(config.get("api_key") or "")
    base_url = str(config.get("base_url") or "")
    model = str(config.get("model") or "")
    timeout = float(config.get("timeout") or 60.0)
    if not api_key or not base_url or not model or not messages:
        return ""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://rugmunch.io",
        "X-Title": "RMI Chat Fallback",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens
        if max_tokens is not None
        else int(config.get("max_tokens") or 512),
        "temperature": float(config.get("temperature") or 0.5),
    }

    # Retry once on transient failures (429, 5xx, timeout)
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=5.0, read=timeout, write=10.0)
            ) as client:
                resp = await asyncio.wait_for(
                    client.post(base_url, headers=headers, json=payload),
                    timeout=timeout,
                )
        except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
            if attempt == 0:
                continue  # retry once
            return ""
        if resp.status_code in (429, 502, 503, 504):
            if attempt == 0:
                continue  # retry once on rate-limit / gateway errors
            return ""
        if resp.status_code != 200:
            return ""
        try:
            raw = resp.json()
        except ValueError:
            return ""
        if not isinstance(raw, dict):
            return ""
        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = ""
        # KiloCode StepFlash puts output in `reasoning` field when `content` is empty
        if not content:
            try:
                content = raw["choices"][0]["message"]["reasoning"]
            except (KeyError, IndexError, TypeError):
                content = ""
        if not isinstance(content, str):
            return ""
        return content.strip()
    return ""


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    task_type: str = "chat",
    temperature: float = 0.5,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """ai_router.chat_completion-compatible wrapper (Phase 2d).

    Vendored modules (briefing) call this with the ai_router signature;
    returns ``{"content": ...}`` or ``{}`` on failure (fail-open).
    """
    tier = "long" if task_type in ("analysis", "deep") else "snappy"
    answer = await chat_with_fallback(messages=messages, tier=tier, max_tokens=max_tokens)
    return {"content": answer} if answer else {}


async def chat_with_fallback(
    messages: list[dict[str, str]],
    tier: str,
    max_tokens: int | None = None,
) -> str:
    """Chat via KiloCode free tier with OpenRouter fallback.

    ``tier`` selects the free provider:
      ``"snappy"`` → KiloCode StepFlash 3.7:free → OR GPT-OSS 20B:free
      ``"long"``   → KiloCode Lightning:free → OR Nemotron Ultra:free
    On ANY failure the call is retried ONCE with the fallback free model,
    then once more with the cheap paid-tier fallback.
    Returns ``""`` when all fail.
    """
    if tier == "snappy":
        config = get_snappy_config()
    elif tier == "long":
        config = get_long_config()
    else:
        return ""  # unknown tier — fail open, no request

    # Try primary (KiloCode)
    answer = await _chat_once(config, messages, max_tokens)
    if answer:
        return answer

    # Try fallback (OpenRouter)
    fallback_config = {
        "model": config.get("fallback_model", ""),
        "base_url": config.get("fallback_base_url", ""),
        "api_key": config.get("fallback_api_key", ""),
        "max_tokens": config.get("max_tokens", 2000),
        "temperature": config.get("temperature", 0.5),
    }
    answer = await _chat_once(fallback_config, messages, max_tokens)
    if answer:
        return answer

    # Last resort: cheap fallback
    return await _chat_once(get_cheap_fallback_config(), messages, max_tokens)
