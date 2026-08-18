"""RugMunchBot LLM config + fallback chain — BOT-LOCAL.

Phase 2a extraction of the bits the bot uses from rmi-backend's
``app.llm_config`` (frozen copy of the same logic; env-driven, reads the
bot's own environment — ``OPENROUTER_BACKUP_API_KEY`` etc. come from
``/srv/secrets/rugmunchbot.env``).

Chain (canonical — matches rmi-backend):
  snappy = ``poolside/laguna-s-2.1:free``   (fast chat, OpenRouter free)
  long   = ``nvidia/nemotron-3-ultra-550b-a55b:free`` (deep analysis)
  paid   = ``qwen/qwen-2.5-7b-instruct:lowest`` (last-resort fallback)
On ANY free-tier failure (429 / non-200 / timeout / empty) the call is
retried once with the paid ``:lowest`` model so chat never dies on a
free-tier rate-limit. Returns ``""`` when both fail — callers fail open.
Never raises; never logs the API key.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

OPENROUTER_BASE = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_BACKUP_API_KEY = os.getenv("OPENROUTER_BACKUP_API_KEY", "")


def get_snappy_config() -> dict[str, Any]:
    """Config for fast chat answers (Poolside Laguna S 2.1, FREE via OpenRouter).

    max_tokens 2000 (was 256) — the snappy prompt tells the model to write the
    COMPLETE answer when a question needs depth; the old cap truncated
    detailed answers mid-sentence. Multi-part delivery splits long answers.
    """
    return {
        "model": "poolside/laguna-s-2.1:free",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 2000,
        "temperature": 0.4,
    }


def get_long_config() -> dict[str, Any]:
    """Config for longer/deep answers (NVIDIA Nemotron 3 Ultra 550B, FREE via OpenRouter).

    max_tokens 3000 (was 1024) — deep analysis needs the budget to finish the
    full structure (direct answer → mechanism → figures → caveat).
    """
    return {
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 3000,
        "temperature": 0.7,
    }


def get_cheap_fallback_config() -> dict[str, Any]:
    """Bottom-of-the-barrel PAID fallback when the free tiers rate-limit.

    Uses the ``:lowest`` modifier so OpenRouter routes to the cheapest host.
    ~$0.05-0.07 / 1M tokens. Swap the model here to change the fallback.
    """
    return {
        "model": "qwen/qwen-2.5-7b-instruct:lowest",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "max_tokens": 2000,
        "temperature": 0.5,
    }


async def _chat_once(
    config: dict[str, Any],
    messages: list[dict[str, str]],
    max_tokens: int | None,
) -> str:
    """One OpenAI-compatible chat POST to ``config``. Returns stripped content
    or ``""``. Never raises."""
    api_key = str(config.get("api_key") or "")
    base_url = str(config.get("base_url") or "")
    model = str(config.get("model") or "")
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
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0, read=20.0, write=10.0)
        ) as client:
            resp = await asyncio.wait_for(
                client.post(base_url, headers=headers, json=payload),
                timeout=20.0,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
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
        return ""
    if not isinstance(content, str):
        return ""
    return content.strip()


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
    """Chat via the free OpenRouter tier, degrading to the cheap PAID model.

    ``tier`` selects the free provider: ``"snappy"`` (laguna-s-2.1:free) or
    ``"long"`` (nemotron-3-ultra:free). On ANY failure the call is retried
    ONCE with the paid ``:lowest`` fallback. Returns ``""`` when both fail.
    """
    if tier == "snappy":
        config = get_snappy_config()
    elif tier == "long":
        config = get_long_config()
    else:
        return ""  # unknown tier — fail open, no request

    answer = await _chat_once(config, messages, max_tokens)
    if answer:
        return answer
    return await _chat_once(get_cheap_fallback_config(), messages, max_tokens)
