"""RugMunchBot LLM config + fallback chain — BOT-LOCAL.

Phase 2a extraction of the bits the bot uses from rmi-backend's
``app.llm_config`` (frozen copy of the same logic; env-driven, reads the
bot's own environment — ``OPENROUTER_BACKUP_API_KEY`` and
``KILOCODE_API_KEY`` come from ``/srv/secrets/rugmunchbot.env``).

Chain (canonical — updated 2026-08-19):
  snappy = ``stepfun/step-3.7-flash:free`` via KiloCode (fast chat, free)
  long   = ``nvidia/nemotron-3.5-lightning:free`` via KiloCode (deep analysis, fast)
  paid   = ``nvidia/nemotron-3-super-120b-a12b:free`` via OpenRouter (last-resort fallback)
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
# 2026-08-25: OpenCode Zen — the only fallback lane that survives a full
# OpenRouter outage (the old last-resort was ALSO OpenRouter, so an OR
# outage killed every rung of the chain). lightning = short/snappy,
# ultra-550b = long/reasoned.
ZEN_BASE = os.getenv("OPENCODE_ZEN_BASE", "https://opencode.ai/zen/v1")
ZEN_API_KEY = os.getenv("OPENCODE_ZEN_API_KEY", "")


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
        "fallback_model": "nvidia/nemotron-3-super-120b-a12b:free",
        "fallback_base_url": f"{OPENROUTER_BASE}/chat/completions",
        "fallback_api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 2000,
        "temperature": 0.3,
        "timeout": 40.0,
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


def get_kilo_nemotron_fallback_config(tier: str) -> dict[str, Any]:
    """KiloCode free Nemotron rung — different MODEL than the tier primaries.

    2026-08-25 decision: when the tier primary (StepFlash/Lightning) AND
    OpenRouter both fail, try the OTHER KiloCode Nemotron before leaving
    KiloCode entirely — a rate-limit on one model rarely affects its sibling.

    ``snappy`` → ``nvidia/nemotron-3.5-lightning:free`` (short/fast)
    ``long``   → ``nvidia/nemotron-3-ultra-550b-a55b:free`` (deep reasoned)
    """
    model = (
        "nvidia/nemotron-3-ultra-550b-a55b:free"
        if tier == "long"
        else "nvidia/nemotron-3.5-lightning:free"
    )
    return {
        "model": model,
        "base_url": f"{KILOCODE_BASE}/chat/completions",
        "api_key": KILOCODE_API_KEY,
        "max_tokens": 3000 if tier == "long" else 2000,
        "temperature": 0.5,
        "timeout": 90.0,
    }


def get_zen_fallback_config(tier: str) -> dict[str, Any]:
    """OpenCode Zen fallback — survives a full OpenRouter outage (2026-08-25).

    ``snappy`` → ``nemotron-3.5-lightning-free`` (fast, short answers)
    ``long``   → ``nemotron-3-ultra-free`` (550B, deep reasoned analysis)

    Free tier, independent daily budget from OpenRouter/KiloCode. 90s
    timeout — ultra can be slow under load but is the last resort.
    """
    model = "nemotron-3-ultra-free" if tier == "long" else "nemotron-3.5-lightning-free"
    return {
        "model": model,
        "base_url": f"{ZEN_BASE}/chat/completions",
        "api_key": ZEN_API_KEY,
        "max_tokens": 3000 if tier == "long" else 2000,
        "temperature": 0.5,
        "timeout": 90.0,
    }


def get_cheap_fallback_config() -> dict[str, Any]:
    """Free last-resort fallback when primary tiers fail.

    Uses Nemotron 3 Super 120B via OpenRouter free tier. Zero cost.
    (2026-08-25: openai/gpt-oss-20b:free was retired by OpenRouter —
    "unavailable for free" 404; slug verified live against /models.)
    30s timeout — should be fast enough for a lightweight model.
    """
    return {
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
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
        # Never emit chain-of-thought (StepFlash/DeepSeek-style reasoning).
        "reasoning": {"enabled": False},
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
        # NEVER fall back to the `reasoning` field as the answer — that is the
        # model's chain-of-thought and must not be surfaced to users. If the
        # provider returned only reasoning (content empty), treat it as a
        # failed call so the fallback chain picks a non-reasoning model.
        if not isinstance(content, str):
            return ""
        content = content.strip()
        if content:
            return content
        # Reasoning-starvation guard (2026-08-25): reasoning models (StepFlash,
        # nemotron-ultra, R1-class) burn the token budget on chain-of-thought
        # before emitting any content — empty string with completion_tokens
        # spent means the budget was too small, not a provider failure. Retry
        # ONCE at double budget instead of failing the whole rung.
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        spent = int(usage.get("completion_tokens") or 0)
        if spent > 0 and attempt == 0:
            retry_cfg = dict(config)
            retry_cfg["max_tokens"] = min(
                max(int(retry_cfg.get("max_tokens") or 512) * 2, 1024), 4000
            )
            result = await _chat_once(retry_cfg, messages, int(retry_cfg["max_tokens"]))
            if result:
                return result
        return ""
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
    """Chat via KiloCode free tier with OpenRouter + Zen fallbacks.

    ``tier`` selects the free provider:
      ``"snappy"`` → KiloCode StepFlash 3.7:free → OR GPT-OSS 20B:free
                     → Zen nemotron-3.5-lightning-free
      ``"long"``   → KiloCode Lightning:free → OR Nemotron Ultra:free
                     → Zen nemotron-3-ultra-free (550B)
    On ANY failure the call is retried ONCE with the fallback free model,
    then the Zen lane — the only rung that works when OpenRouter itself is
    down (2026-08-25). Returns ``""`` when all fail.
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

    # KiloCode sibling Nemotron (lightning for short, ultra-550b for long)
    answer = await _chat_once(get_kilo_nemotron_fallback_config(tier), messages, max_tokens)
    if answer:
        return answer

    # Last resort: OpenCode Zen (independent of OpenRouter availability)
    return await _chat_once(get_zen_fallback_config(tier), messages, max_tokens)
