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
# 2026-08-29: DeepInfra — cheap primary chat (Qwen3.5-9B ~$0.08/Mtok). The
# reasoning model must be called with reasoning.enabled=false (see _chat_once).
DEEPINFRA_BASE = os.getenv("DEEPINFRA_BASE", "https://api.deepinfra.com/v1/openai")
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "")
# 2026-08-30: DeepInfra vision — Qwen3-VL (multimodal). The task target was
# "Qwen3-VL-4B", but DeepInfra does NOT host a 4B VL checkpoint (verified
# against /v1/models 2026-08-30: only Qwen/Qwen3-VL-30B-A3B-Instruct and
# Qwen/Qwen3-VL-235B-A22B-Instruct). The 30B-A3B is the cheapest VL on
# offer and is the working default; set DEEPINFRA_VISION_MODEL to a real
# Qwen3-VL-4B id if/when DeepInfra or another cheap provider hosts it.
DEEPINFRA_VISION_MODEL = os.getenv("DEEPINFRA_VISION_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct")



# ── Local LLM lane (Talos llama.cpp Qwen3-8B) ────────────────────────────
# 2026-09-01 doctrine: light content tasks (commentary lines, title
# rewrites, classification, short summaries) run LOCAL-FIRST on the
# self-hosted Qwen3-8B-Instruct (:8009) — zero API cost, zero rate limit,
# ~1-3s on CPU. Quality-critical tasks (flagship drafts, full analysis)
# stay on the hosted writer. RMI_LOCAL_LLM_ENABLED=false kills the lane
# (everything falls through to the free-API chain).
LOCAL_LLM_ENABLED = os.getenv("RMI_LOCAL_LLM_ENABLED", "true").lower() in ("1", "true", "yes")
LOCAL_LLM_URL = os.getenv("RMI_LOCAL_LLM_URL", "http://127.0.0.1:8009/v1/chat/completions")
LOCAL_LLM_MODEL = os.getenv("RMI_LOCAL_LLM_MODEL", "qwen3-8b-instruct")
#: Task classes that are cheap enough + short enough for the local 8B.
#: Everything else (flagship drafts, deep analysis) stays on the hosted lane.
LOCAL_FIRST_TASKS = frozenset(
    os.getenv(
        "RMI_LOCAL_FIRST_TASKS",
        "commentary,title_rewrite,classify,summary_short,social_caption",
    ).split(",")
)


def get_local_config(max_tokens: int = 700) -> dict[str, Any]:
    """Local Qwen3-8B llama.cpp config — the zero-cost light-task lane."""
    return {
        "model": LOCAL_LLM_MODEL,
        "base_url": LOCAL_LLM_URL,
        "api_key": "local",  # llama.cpp ignores auth
        "max_tokens": max_tokens,
        "temperature": 0.4,
        "timeout": 45.0,
    }


def is_local_first_task(task_class: str) -> bool:
    """True when *task_class* should try the local 8B before the API chain."""
    return LOCAL_LLM_ENABLED and task_class in LOCAL_FIRST_TASKS


def get_snappy_config() -> dict[str, Any]:
    """Config for fast chat answers.

    2026-08-29: DeepInfra Qwen3.5-9B primary (~$0.08/Mtok, ~0.7s, RAG-friendly).
    Falls back to KiloCode StepFlash:free if DeepInfra is down.
    max_tokens 2000 — the snappy prompt tells the model to write the
    COMPLETE answer when a question needs depth; the old cap truncated
    detailed answers mid-sentence. Multi-part delivery splits long answers.
    """
    # 2026-09-01 doctrine: FREE-FIRST. KiloCode StepFlash:free primary;
    # the paid DeepInfra Qwen3.5-9B is the FINAL rung (see
    # get_paid_final_config) — only hit when every free lane is down.
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


def get_paid_final_config(tier: str) -> dict[str, Any]:
    """FINAL paid rung — DeepInfra Qwen3.5-9B.

    2026-09-01 doctrine: every free lane (KiloCode, OpenRouter, Zen) is
    tried FIRST; this paid lane fires only when all of them fail. ~$0.08/Mtok
    — cheap insurance that chat NEVER goes dark. Same model the writer
    ladder uses for flagship drafts, so quality is proven.
    """
    return {
        "model": "Qwen/Qwen3.5-9B",
        "base_url": f"{DEEPINFRA_BASE}/chat/completions",
        "api_key": DEEPINFRA_API_KEY,
        "max_tokens": 2000 if tier == "snappy" else 3000,
        "temperature": 0.3 if tier == "snappy" else 0.7,
        "timeout": 60.0,
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
    # 2026-09-01: snappy -> laguna-s-2.1-free (verified 200 live; the old
    # nemotron-3.5-lightning-free lane hung 90-180s and starved the chain)
    model = "nemotron-3-ultra-free" if tier == "long" else "laguna-s-2.1-free"
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
    *,
    task_class: str = "",
) -> str:
    """Chat via the tier chain — with a LOCAL-FIRST rung for light tasks.

    2026-09-01 doctrine: when ``task_class`` is a light class (commentary,
    title_rewrite, classify, summary_short, social_caption) the call tries
    the self-hosted Qwen3-8B (:8009) FIRST — zero API cost, no rate limit.
    On any local failure (down, timeout, empty) it falls through to the
    hosted free-API chain below, so quality never depends on the local box.
    Quality-critical task classes skip the local rung entirely.

    ``tier`` selects the hosted provider:
      ``"snappy"`` → DeepInfra Qwen3.5-9B → KiloCode StepFlash:free → OR
                     GPT-OSS 20B:free → Zen nemotron-3.5-lightning-free
      ``"long"``   → KiloCode Lightning:free → OR Nemotron Ultra:free
                     → Zen nemotron-3-ultra-free (550B)
    Returns ``""`` when all fail.
    """
    # Local-first rung for light task classes (zero cost, no rate limit)
    if is_local_first_task(task_class):
        answer = await _chat_once(get_local_config(max_tokens or 700), messages, max_tokens)
        if answer:
            return answer
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

    # Last free resort: OpenCode Zen (independent of OpenRouter availability)
    answer = await _chat_once(get_zen_fallback_config(tier), messages, max_tokens)
    if answer:
        return answer

    # FINAL paid rung — DeepInfra Qwen3.5-9B (only when every free lane is down)
    return await _chat_once(get_paid_final_config(tier), messages, max_tokens)


def get_vision_config() -> dict[str, Any]:
    """DeepInfra Qwen3-VL vision config — image+text understanding (multimodal).

    Used for screenshot / chart / contract-image understanding in chat. The
    OpenAI-compatible */chat/completions payload carries an image_url part;
    ``vision_chat`` below builds the content. Cheap: Qwen3-VL-30B-A3B-Instruct
    is the smallest DeepInfra VL checkpoint (the requested Qwen3-VL-4B is not
    hosted on DeepInfra as of 2026-08-30 — see DEEPINFRA_VISION_MODEL). Uses the
    same DEEPINFRA_BASE as chat; reasoning disabled to keep cost near zero.
    """
    # 2026-09-01 doctrine: FREE-FIRST vision. The OpenRouter omni rung
    # (nemotron-3-nano-omni-30b-a3b-reasoning:free) handles X screenshots +
    # TG shill shots at zero cost (~19s); the paid DeepInfra Qwen3-VL is the
    # quality fallback when the free lane fails or the image is complex.
    return {
        "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "base_url": f"{OPENROUTER_BASE}/chat/completions",
        "api_key": OPENROUTER_BACKUP_API_KEY,
        "fallback_model": DEEPINFRA_VISION_MODEL,
        "fallback_base_url": f"{DEEPINFRA_BASE}/chat/completions",
        "fallback_api_key": DEEPINFRA_API_KEY,
        "max_tokens": 1024,
        "temperature": 0.3,
        "timeout": 90.0,
    }


async def vision_chat(
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Multimodal vision chat via DeepInfra Qwen3-VL.

    ``messages`` use the OpenAI multimodal shape — a ``content`` that is a list
    of parts ``[{"type": "text", "text": ...}, {"type": "image_url",
    "image_url": {"url": ...}}]``. Returns ``{"content": ...}`` or ``{}`` on
    failure (fail-open, never raises). Mirrors ``chat_completion`` semantics so
    callers can treat vision answers identically.
    """
    config = get_vision_config()
    api_key = str(config.get("api_key") or "")
    base_url = str(config.get("base_url") or "")
    model = str(config.get("model") or "")
    timeout = float(config.get("timeout") or 60.0)
    if not api_key or not base_url or not model or not messages:
        return {}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://rugmunch.io",
        "X-Title": "RMI Vision",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens if max_tokens is not None else int(config.get("max_tokens") or 1024),
        "temperature": float(config.get("temperature") or 0.3),
        "reasoning": {"enabled": False},
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0, read=timeout, write=10.0)
        ) as client:
            resp = await asyncio.wait_for(
                client.post(base_url, headers=headers, json=payload),
                timeout=timeout,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
        return {}
    if resp.status_code != 200:
        return {}
    try:
        raw = resp.json()
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {}
    if not isinstance(content, str):
        return {}
    content = content.strip()
    return {"content": content} if content else {}
