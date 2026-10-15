"""RugMunchBot LLM config + fallback chain — BOT-LOCAL.

Phase 2a extraction of the bits the bot uses from rmi-backend's
``app.llm_config`` (frozen copy of the same logic; env-driven, reads the
bot's own environment — ``OPENROUTER_BACKUP_API_KEY`` and
``KILOCODE_API_KEY`` come from ``/srv/secrets/rugmunchbot.env``).

Chain (canonical — rewritten 2026-09-18 after a 17-candidate free benchmark):
  rung 0 = local ``qwythos-9b`` on Talos :8009 (our model, free, private)
  snappy = ``poolside/laguna-s-2.1:free`` via KiloCode (0.8-3s, ZERO reasoning burn)
  long   = ``nvidia/nemotron-3-ultra-550b-a55b:free`` via KiloCode (structured deep)
  then   = OpenRouter ``nemotron-3-super-120b-a12b:free``
  then   = KiloCode alternate (ultra-550b / laguna)
  then   = KiloCode ``kilo-auto/free`` (gateway picks a healthy free model)
  paid   = DeepInfra ``Qwen/Qwen3.5-9B`` (last resort only)
On ANY free-tier failure (429 / non-200 / timeout / empty) the next rung is
tried so chat never dies on a free-tier rate-limit. Returns ``""`` when all
fail — callers fail open. Never raises; never logs the API key.

Retired 2026-09-18, do NOT re-add: ``stepfun/step-3.7-flash:free`` (returns
EMPTY content below ~2000 max_tokens — it spends the whole budget on hidden
chain-of-thought; 237 rt @200, 665 rt @700), ``nvidia/nemotron-3.5-lightning:free``
(burns 218-691 rt and answers with "Here's a thinking process: ..."),
``z-ai/glm-5.2:free`` (empty at 200/700 rt), ``google/gemma-4-*:free`` (404),
``openai/gpt-oss-20b`` (empty on deep), ``cohere/north-mini-code:free``
(wrong domain — described a web-form honeypot). See
fleet-infra/docs/MODEL-TIERS-AND-ROUTING-2026-09-18.md for the full table.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx

from rugmunchbot.support.content_lint import strip_reasoning

logger = logging.getLogger(__name__)


# ── Egress guard (2026-09-14) — public outputs must never leak internals ──
# Bots it on: hostnames/IPs, file paths, internal service names, secrets.
# The guard is the LAST line of defense behind prompt hardening + privilege
# isolation; a blocked answer is replaced by a safe fallback (never leaks).
_EGRESS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # tailnet IPs
    re.compile(r"\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"),  # docker nets
    re.compile(r"\b127\.0\.0\.1\b|\blocalhost:\d+\b"),
    re.compile(r"/srv/|/root/|/etc/systemd|\.venv/"),
    re.compile(
        r"\b(gopass|rugbrain|contextcrypto|rmi-codebase|rmi-backend|forgejo|"
        r"tailscale|llama-?cpp|llama-chat|qwen3\.6|deepinfra|openrouter|"
        r"master_skills|codebase_reference_v2|security_patterns|news_v2|"
        r"chat-bridge|secure_launcher)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{10,}|gopass)\b"),
)
_EGRESS_FALLBACK = (
    "I can't share that — but I can help with token analysis, market data, "
    "or security questions. What would you like to check?"
)
_egress_hits = 0  # observability counter (journal-visible via warnings)


def _egress_guard(text: str) -> str:
    """Block internal-infrastructure leakage in public-facing outputs."""
    global _egress_hits
    if os.getenv("RMI_EGRESS_GUARD", "on").lower() in ("0", "false", "off"):
        return text
    for pat in _EGRESS_PATTERNS:
        if pat.search(text):
            _egress_hits += 1
            logger.warning(
                "egress_guard_blocked pattern=%s hits=%d", pat.pattern[:40], _egress_hits
            )
            return _EGRESS_FALLBACK
    return text


def _clean_answer(answer: str) -> str:
    """Strip LLM chain-of-thought from a chat answer (2026-09-02 incident).

    Applied at the single choke point (``chat_with_fallback``) so every
    writer lane — daily publishers, scan commentary, digests, chat bridge —
    is protected, not just the rugbrain drafts. When nothing clean can be
    salvaged the ORIGINAL is returned: gated callers still hard-reject it
    via the quality gate, and the chat bridge degrades instead of breaking.
    """
    if not answer:
        return answer
    cleaned = strip_reasoning(answer)
    out = cleaned if cleaned.strip() else answer
    return _egress_guard(out)


OPENROUTER_BASE = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_BACKUP_API_KEY = os.getenv("OPENROUTER_BACKUP_API_KEY", "")
KILOCODE_BASE = os.getenv("KILOCODE_BASE", "http://127.0.0.1:18091/api/gateway")
# 2026-10-15: hosted FREE primary lane (owner decision) — one model for chat AND
# content writing, vision-capable, ~320 tok/s. Replaces the local Qwythos lane
# entirely (see RMI_LOCAL_LLM_ENABLED, which now defaults off) and the local
# KiloCode gateway as the snappy primary.
GROQ_BASE = os.getenv("GROQ_BASE", "https://api.groq.com/openai/v1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
# Free fallback #1/#2: Gemini (openai-compatible shim), then the OpenRouter free ids.
GEMINI_BASE = os.getenv("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta/openai")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
KILOCODE_API_KEY = os.getenv("KILOCODE_API_KEY", "")
# 2026-09-18: OpenCode Zen lane RETIRED. Its free tier is session-locked to the
# opencode app — calling it from the bot (a non-opencode client) returns
# "free tier can only be used in opencode". The last free rung is now KiloCode,
# which is a real open gateway with no client restriction.
# See get_kilo_final_fallback_config().
# (The OPENCODE_ZEN_* secrets are now unused by this module and can be retired.)
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


# ── Local LLM lane (Talos llama.cpp Qwythos-9B Claude-Mythos) ─────────────────────
# 2026-09-01 doctrine: light content tasks (commentary lines, title
# rewrites, classification, short summaries) run LOCAL-FIRST on the
# self-hosted Qwythos-9B Claude-Mythos (:8009) — zero API cost, zero
# rate limit, ~8-10 tok/s on CPU (2026-09-18 Qwythos swap). Quality-critical tasks
# stay on the hosted writer. RMI_LOCAL_LLM_ENABLED=false kills the lane
# (everything falls through to the free-API chain).
LOCAL_LLM_ENABLED = os.getenv("RMI_LOCAL_LLM_ENABLED", "false")  # 2026-10-15: OFF by default — Qwythos retired.lower() in ("1", "true", "yes")
LOCAL_LLM_URL = os.getenv("RMI_LOCAL_LLM_URL", "http://127.0.0.1:8009/v1/chat/completions")
LOCAL_LLM_MODEL = os.getenv("RMI_LOCAL_LLM_MODEL", "qwythos-9b")
#: Hard token cap for the local-first consumer-chat rung. A CPU-only 9B at
#: ~8-10 tok/s must stay short to keep chat responsive; anything longer falls
#: through to the free hosted chain instead of stalling the reply.
LOCAL_CHAT_MAX_TOKENS = int(os.getenv("RMI_LOCAL_CHAT_MAX_TOKENS", "700"))
#: Fail-fast timeout for the local rung on the consumer-chat path. Measured
#: 2026-09-18: the 9B answers a SHORT question in ~4.8s but a DEEP one takes
#: ~78s on CPU — so we cap the wait and let the free chain answer instead. The
#: 150s default remains for batch/light-task callers via get_local_config().
LOCAL_CHAT_TIMEOUT_S = float(os.getenv("RMI_LOCAL_CHAT_TIMEOUT_S", "15"))

#: Capability router front-path (2026-09-18). When on, chat_with_fallback first
#: asks model_router which capability the message is (one local embedding,
#: ~300-490 ms, free) and walks that capability's LOCKED model ladder. Falls
#: through to the tier chain below if the router is unavailable or every rung
#: fails. Set RMI_CAPABILITY_ROUTER=false to force the legacy tier chain.
RMI_CAPABILITY_ROUTER = os.getenv("RMI_CAPABILITY_ROUTER", "true").lower() in ("1", "true", "yes")
#: Max rungs to try from the capability ladder — bounds worst-case latency
#: (a deep rung can take 120s; five of them would be unusable).
CAPABILITY_MAX_ATTEMPTS = int(os.getenv("RMI_CAPABILITY_MAX_ATTEMPTS", "4"))
#: Task classes cheap/short enough to prefer the local workhorse. Since
#: 2026-09-18 the local lane is ALSO tried first for consumer chat (snappy);
#: flagship drafts and deep analysis still stay on the hosted lane.
LOCAL_FIRST_TASKS = frozenset(
    os.getenv(
        "RMI_LOCAL_FIRST_TASKS",
        "commentary,title_rewrite,classify,summary_short,social_caption",
    ).split(",")
)


def get_local_config(max_tokens: int = 700, timeout: float = 150.0) -> dict[str, Any]:
    """Local Qwythos-9B llama.cpp config — our own model, the zero-cost lane.

    2026-09-18: Qwythos-9B Claude-Mythos (MTP Q6_K) replaced the old 8B/35B.
    Runs in INSTRUCT mode — ``enable_thinking=false`` is set server-side in
    llama-chat.service, so there is no reasoning preamble to strip.

    ``timeout`` matters: measured 2026-09-18, the 9B does a SHORT answer in
    ~4.8s but a DEEP one takes ~78s on CPU. Consumer chat must therefore use a
    short timeout (LOCAL_CHAT_TIMEOUT_S) and fail fast into the free chain rather
    than stall the reply; batch/light-task callers can keep the 150s default.
    """
    return {
        "model": LOCAL_LLM_MODEL,
        "base_url": LOCAL_LLM_URL,
        "api_key": "local",  # llama.cpp ignores auth
        "max_tokens": max_tokens,
        "temperature": 0.4,
        "timeout": timeout,
    }


def is_local_first_task(task_class: str) -> bool:
    """True when *task_class* should try our local Qwythos-9B before the API chain."""
    return LOCAL_LLM_ENABLED and task_class in LOCAL_FIRST_TASKS


def get_snappy_config() -> dict[str, Any]:
    """Config for fast chat answers — the SNAPPY tier.

    2026-09-18 (measured, bench 17 candidates x 2 tasks): primary is
    ``poolside/laguna-s-2.1:free`` via KiloCode. It was the only free model
    that was BOTH fast and clean on every run — 0.8-3 s, correct domain
    answers, and **zero reasoning-token burn**.

    Dropped ``stepfun/step-3.7-flash:free`` (the previous primary): measured
    it returns EMPTY content below ~2000 max_tokens because it spends the whole
    budget on hidden chain-of-thought (237 rt @200, 665 rt @700 -> content "").
    It also leaks a "Here's a thinking process:" preamble when it does answer.
    We are not budgeting around a model that answers nothing.

    OpenRouter fallback stays Nemotron Super 120B (verified working).
    max_tokens 3000 — headroom so a reasoning-capable fallback still emits content.
    """
    return {
        "model": GROQ_MODEL,
        "base_url": f"{GROQ_BASE}/chat/completions",
        "api_key": GROQ_API_KEY,
        "fallback_model": GEMINI_MODEL,
        "fallback_base_url": f"{GEMINI_BASE}/chat/completions",
        "fallback_api_key": GEMINI_API_KEY,
        # 2026-10-15: 6000 (was 3000). The local 9B managed ~9 tok/s, so a long
        # answer meant a long wait; hosted Qwen does ~320 tok/s, so output length
        # is no longer the thing that makes a reply feel slow.
        "max_tokens": 6000,
        "temperature": 0.3,
        "timeout": 40.0,
    }


def get_long_config() -> dict[str, Any]:
    """Config for deep analysis answers — the DEEP tier.

    2026-09-18 (measured): primary is ``nvidia/nemotron-3-ultra-550b-a55b:free``.
    On the deep-domain bench it produced structured, metric-naming answers with
    only 28-73 reasoning tokens (7-27 s) — the best deep output of the free set.

    Dropped ``nvidia/nemotron-3.5-lightning:free``: it burned 218-691 reasoning
    tokens and answered with "Here's a thinking process: ..." — i.e. it leaked
    chain-of-thought instead of an answer. Unusable as a user-facing tier.

    max_tokens 3000 keeps the full structure (answer -> figures -> evidence ->
    caveat -> close) inside budget. 120s timeout: ultra can be slow under load.
    """
    return {
        "model": GROQ_MODEL,
        "base_url": f"{GROQ_BASE}/chat/completions",
        "api_key": GROQ_API_KEY,
        "fallback_model": GEMINI_MODEL,
        "fallback_base_url": f"{GEMINI_BASE}/chat/completions",
        "fallback_api_key": GEMINI_API_KEY,
        # Same model as snappy on purpose: ONE lane for chat and content writing
        # (no second setup). 8000 tokens of headroom for long/analysis answers.
        "max_tokens": 8000,
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

    2026-08-25 decision: when the tier primary AND OpenRouter both fail, try the
    OTHER KiloCode model before leaving KiloCode entirely — a rate-limit on one
    model rarely affects its sibling. Alternates the two best benches:

    ``snappy`` → ``nvidia/nemotron-3-ultra-550b-a55b:free`` (1.1s on the snappy
                 bench, correct, only 16 rt)
    ``long``   → ``poolside/laguna-s-2.1:free`` (clean structured deep answers,
                 0 reasoning burn)

    Dropped ``nvidia/nemotron-3.5-lightning:free`` (2026-09-18): 218-691
    reasoning tokens and it answered with "Here's a thinking process: ...".
    """
    model = (
        "poolside/laguna-s-2.1:free" if tier == "long" else "nvidia/nemotron-3-ultra-550b-a55b:free"
    )
    return {
        "model": model,
        "base_url": f"{KILOCODE_BASE}/chat/completions",
        "api_key": KILOCODE_API_KEY,
        "max_tokens": 3000 if tier == "long" else 2000,
        "temperature": 0.5,
        "timeout": 90.0,
    }


def get_kilo_final_fallback_config(tier: str) -> dict[str, Any]:
    """KiloCode final free rung — replaces the retired OpenCode Zen lane (2026-09-18).

    Why it exists: a rung that survives a full OpenRouter outage. Zen used to do
    that but is session-locked to the opencode app, so the bot got
    "free tier can only be used in opencode" and the rung was dead weight.

    This rung defers the choice to the gateway: ``kilo-auto/free`` lets Kilo route
    to whichever free model is healthy right now, which is the right behaviour for
    a last-resort rung (verified working live 2026-09-18; can 429 under load, in
    which case the paid DeepInfra rung is the final backstop).

    Full 17-candidate free benchmark 2026-09-18 (2 tasks each) — rejects recorded
    so nobody re-adds them: ``stepfun/*`` (empty content below ~2000 max_tokens,
    burns budget on hidden CoT), ``z-ai/glm-5.2:free`` (empty at 200/700 rt),
    ``google/gemma-4-*:free`` (404 — listed but not served),
    ``nvidia/nemotron-3.5-lightning:free`` (leaks "thinking process"),
    ``openai/gpt-oss-20b`` + ``-safeguard-20b`` (empty on deep),
    ``inclusionai/ling-3.0-flash-sante:free`` (empty on deep),
    ``cohere/north-mini-code:free`` (wrong domain: described a web-form honeypot),
    ``thinkingmachines/*`` (daily cap), ``qwen/*`` (keep off the consumer path).
    """
    del tier  # one config for both tiers — the gateway picks the model
    return {
        "model": "kilo-auto/free",
        "base_url": f"{KILOCODE_BASE}/chat/completions",
        "api_key": KILOCODE_API_KEY,
        "max_tokens": 3000,
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
                "User-Agent": "rugmunchbot/1.0 (+https://rugmunch.io)",
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
    the self-hosted Qwythos-9B (:8009) FIRST — zero API cost, no rate limit.
    On any local failure (down, timeout, empty) it falls through to the
    hosted free-API chain below, so quality never depends on the local box.
    Quality-critical task classes skip the local rung entirely.

    ``tier`` selects the hosted chain (only reached if our own model fails):
      ``"snappy"`` → local Qwythos-9B (:8009, OUR MODEL) → KiloCode
                     laguna-s-2.1:free → OpenRouter Nemotron Super:free →
                     KiloCode ultra-550b:free → KiloCode kilo-auto/free
      ``"long"``   → KiloCode ultra-550b:free → OpenRouter Nemotron Super:free
                     → KiloCode laguna-s-2.1:free → KiloCode kilo-auto/free
      final (both) → DeepInfra Qwen3.5-9B (paid, last resort)
    Returns ``""`` when all fail.
    """
    # ── Capability-router front-path (2026-09-18) ──────────────────────────
    # The capability -> model decision is LOCKED offline (model_router.py), so
    # per call we pay exactly ONE local embedding (~300-490 ms, free) and then
    # walk the locked ladder for that capability. No reranker call, no API call
    # to decide. If the router is unavailable (embedder down) or every rung
    # fails, we fall through to the tier chain below — degrade, never break.
    if RMI_CAPABILITY_ROUTER:
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = str(msg.get("content") or "")
                break
        if user_text:
            ladder: list[dict[str, Any]] = []
            try:
                from . import model_router  # local import: keeps import cost off cold paths

                ladder = model_router.route(user_text)
            except Exception as exc:  # router must never break chat
                logger.warning("capability_router_unavailable", extra={"err": str(exc)[:120]})
            for cfg in ladder[:CAPABILITY_MAX_ATTEMPTS]:
                answer = await _chat_once(cfg, messages, int(cfg.get("max_tokens") or 2000))
                if answer:
                    logger.info(
                        "capability_route",
                        extra={
                            "capability": cfg.get("_capability"),
                            "confidence": cfg.get("_confidence"),
                            "target": cfg.get("_target"),
                        },
                    )
                    return _clean_answer(answer)

    # ── Rung 0 (2026-09-18 doctrine): OUR OWN MODEL FIRST ──────────────────
    # Local Qwythos-9B on Talos :8009 — zero API cost, zero rate limit, private.
    # Tried first for consumer chat (snappy) and for light task classes. The
    # token budget is capped so a CPU-only 9B (~8-10 tok/s) can't stall the
    # reply; if it is slow/empty/unavailable the free chain below takes over.
    # Consumer chat uses a SHORT timeout: measured ~78s for a deep answer, which
    # must fail fast into the hosted chain rather than block the reply.
    if tier == "snappy" or is_local_first_task(task_class):
        local_budget = min(max_tokens or LOCAL_CHAT_MAX_TOKENS, LOCAL_CHAT_MAX_TOKENS)
        local_cfg = get_local_config(local_budget, timeout=LOCAL_CHAT_TIMEOUT_S)
        answer = await _chat_once(local_cfg, messages, local_budget)
        if answer:
            return _clean_answer(answer)
    if tier == "snappy":
        config = get_snappy_config()
    elif tier == "long":
        config = get_long_config()
    else:
        return ""  # unknown tier — fail open, no request

    # Try primary (KiloCode)
    answer = await _chat_once(config, messages, max_tokens)
    if answer:
        return _clean_answer(answer)

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
        return _clean_answer(answer)

    # KiloCode sibling Nemotron (lightning for short, ultra-550b for long)
    answer = await _chat_once(get_kilo_nemotron_fallback_config(tier), messages, max_tokens)
    if answer:
        return _clean_answer(answer)

    # Last free resort: KiloCode (independent of OpenRouter availability).
    # 2026-09-18: replaced the retired OpenCode Zen lane — Zen's free tier is
    # session-locked to the opencode app and rejected the bot with
    # "free tier can only be used in opencode".
    answer = await _chat_once(get_kilo_final_fallback_config(tier), messages, max_tokens)
    if answer:
        return _clean_answer(answer)

    # FINAL paid rung — DeepInfra Qwen3.5-9B (only when every free lane is down)
    return _clean_answer(await _chat_once(get_paid_final_config(tier), messages, max_tokens))


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
                "User-Agent": "rugmunchbot/1.0 (+https://rugmunch.io)",
"HTTP-Referer": "https://rugmunch.io",
        "X-Title": "RMI Vision",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens
        if max_tokens is not None
        else int(config.get("max_tokens") or 1024),
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
